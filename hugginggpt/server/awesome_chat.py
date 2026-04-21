import base64
import copy
from io import BytesIO
import io
import os
import random
import time
import traceback
import uuid
import requests
import re
import json
import logging
import argparse
import yaml
from PIL import Image, ImageDraw
from diffusers.utils import load_image
from pydub import AudioSegment
import threading
from queue import Queue
import flask
from flask import request, jsonify
import waitress
from flask_cors import CORS, cross_origin
from get_token_ids import get_token_ids_for_task_parsing, get_token_ids_for_choose_model, count_tokens, get_max_context_length
from huggingface_hub.inference_api import InferenceApi
from huggingface_hub.inference_api import ALL_TASKS
from device_integration import EndpointDeviceAdapter, DeviceOrchestrator
from claude_adapter import send_request_claude

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=str, default="configs/config.default.yaml")
parser.add_argument("--mode", type=str, default="cli")
parser.add_argument("--voice", action="store_true",
                    help="Enable side-by-side voice input/output in CLI mode. "
                         "Type OR speak — whichever comes first.")
parser.add_argument("--no-wake-word", action="store_true",
                    help="With --voice, treat every utterance as a command "
                         "(no 'Hey Jarvis' gating).")
args = parser.parse_args()

if __name__ != "__main__":
    args.config = "configs/config.gradio.yaml"
    args.mode = "gradio"

config = yaml.load(open(args.config, "r"), Loader=yaml.FullLoader)

os.makedirs("logs", exist_ok=True)
os.makedirs("public/images", exist_ok=True)
os.makedirs("public/audios", exist_ok=True)
os.makedirs("public/videos", exist_ok=True)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
if not config["debug"]:
    handler.setLevel(logging.CRITICAL)
logger.addHandler(handler)

log_file = config["log_file"]
if log_file:
    filehandler = logging.FileHandler(log_file)
    filehandler.setLevel(logging.DEBUG)
    filehandler.setFormatter(formatter)
    logger.addHandler(filehandler)

LLM = config["model"]
use_completion = config["use_completion"]

# consistent: wrong msra model name 
LLM_encoding = LLM
if config["dev"] and LLM == "gpt-3.5-turbo":
    LLM_encoding = "text-davinci-003"
# Claude models aren't in tiktoken — fall back to gpt-3.5-turbo encoding for token counting only
OPENAI_TIKTOKEN_MODELS = ["gpt-4", "gpt-3.5", "text-davinci", "davinci", "curie", "babbage", "ada"]
if not any(m in LLM.lower() for m in OPENAI_TIKTOKEN_MODELS):
    LLM_encoding = "gpt-3.5-turbo"
task_parsing_highlight_ids = get_token_ids_for_task_parsing(LLM_encoding)
choose_model_highlight_ids = get_token_ids_for_choose_model(LLM_encoding)

# ENDPOINT	MODEL NAME	
# /v1/chat/completions	gpt-4, gpt-4-0314, gpt-4-32k, gpt-4-32k-0314, gpt-3.5-turbo, gpt-3.5-turbo-0301	
# /v1/completions	text-davinci-003, text-davinci-002, text-curie-001, text-babbage-001, text-ada-001, davinci, curie, babbage, ada

if use_completion:
    api_name = "completions"
else:
    api_name = "chat/completions"

API_TYPE = None
# priority: local > claude > azure > openai
if "dev" in config and config["dev"]:
    API_TYPE = "local"
elif "anthropic" in config:
    API_TYPE = "claude"
elif "azure" in config:
    API_TYPE = "azure"
elif "openai" in config:
    API_TYPE = "openai"
else:
    logger.warning(f"No endpoint specified in {args.config}. The endpoint will be set dynamically according to the client.")

if args.mode in ["test", "cli"]:
    assert API_TYPE, "Only server mode supports dynamic endpoint."

API_KEY = None
API_ENDPOINT = None
if API_TYPE == "local":
    API_ENDPOINT = f"{config['local']['endpoint']}/v1/{api_name}"
elif API_TYPE == "claude":
    # Claude uses its own HTTP endpoint; we don't need API_ENDPOINT here.
    # We read the key from config or env.
    cfg_key = (config.get("anthropic") or {}).get("api_key", "")
    if cfg_key and cfg_key.startswith("sk-ant-"):
        API_KEY = cfg_key
    elif "ANTHROPIC_API_KEY" in os.environ:
        API_KEY = os.getenv("ANTHROPIC_API_KEY")
    else:
        raise ValueError(
            f"Anthropic API key not found. Set anthropic.api_key in {args.config} "
            "or export ANTHROPIC_API_KEY."
        )
    API_ENDPOINT = "https://api.anthropic.com/v1/messages"   # informational
elif API_TYPE == "azure":
    API_ENDPOINT = f"{config['azure']['base_url']}/openai/deployments/{config['azure']['deployment_name']}/{api_name}?api-version={config['azure']['api_version']}"
    API_KEY = config["azure"]["api_key"]
elif API_TYPE == "openai":
    base_url = config["openai"].get("base_url", "https://api.openai.com/v1")
    API_ENDPOINT = f"{base_url}/{api_name}"
    cfg_key = config["openai"]["api_key"]
    if cfg_key and cfg_key not in ("", "REPLACE_WITH_YOUR_OPENAI_API_KEY_HERE"):
        API_KEY = cfg_key
    elif "OPENAI_API_KEY" in os.environ:
        API_KEY = os.getenv("OPENAI_API_KEY")
    else:
        raise ValueError(f"API key not found. Please check your {args.config} file.")

PROXY = None
if config["proxy"]:
    PROXY = {
        "https": config["proxy"],
    }

inference_mode = config["inference_mode"]

INTEGRATIONS = config.get("integrations", {})
PHONE_CFG = INTEGRATIONS.get("phone", {})
COMPUTER_CFG = INTEGRATIONS.get("computer", {})

adapters = {}
if PHONE_CFG.get("enabled"):
    adapters["phone"] = EndpointDeviceAdapter(
        PHONE_CFG.get("base_url"),
        PHONE_CFG.get("token"),
        "Phone",
    )
if COMPUTER_CFG.get("enabled"):
    adapters["computer"] = EndpointDeviceAdapter(
        COMPUTER_CFG.get("base_url"),
        COMPUTER_CFG.get("token"),
        "Computer",
    )
DEVICE_ORCHESTRATOR = DeviceOrchestrator(adapters) if adapters else None

# check the local_inference_endpoint
Model_Server = None
if inference_mode!="huggingface":
    Model_Server = "http://" + config["local_inference_endpoint"]["host"] + ":" + str(config["local_inference_endpoint"]["port"])
    message = f"The server of local inference endpoints is not running, please start it first. (or using `inference_mode: huggingface` in {args.config} for a feature-limited experience)"
    try:
        r = requests.get(Model_Server + "/running")
        if r.status_code != 200:
            raise ValueError(message)
    except:
        raise ValueError(message)


parse_task_demos_or_presteps = open(config["demos_or_presteps"]["parse_task"], "r").read()
choose_model_demos_or_presteps = open(config["demos_or_presteps"]["choose_model"], "r").read()
response_results_demos_or_presteps = open(config["demos_or_presteps"]["response_results"], "r").read()

parse_task_prompt = config["prompt"]["parse_task"]
choose_model_prompt = config["prompt"]["choose_model"]
response_results_prompt = config["prompt"]["response_results"]

parse_task_tprompt = config["tprompt"]["parse_task"]
choose_model_tprompt = config["tprompt"]["choose_model"]
response_results_tprompt = config["tprompt"]["response_results"]

MODELS = [json.loads(line) for line in open("data/p0_models.jsonl", "r").readlines()]
MODELS_MAP = {}
for model in MODELS:
    tag = model["task"]
    if tag not in MODELS_MAP:
        MODELS_MAP[tag] = []
    MODELS_MAP[tag].append(model)
METADATAS = {}
for model in MODELS:
    METADATAS[model["id"]] = model

HUGGINGFACE_HEADERS = {}
if config["huggingface"]["token"] and config["huggingface"]["token"].startswith("hf_"):  # Check for valid huggingface token in config file
    HUGGINGFACE_HEADERS = {
        "Authorization": f"Bearer {config['huggingface']['token']}",
    }
elif "HUGGINGFACE_ACCESS_TOKEN" in os.environ and os.getenv("HUGGINGFACE_ACCESS_TOKEN").startswith("hf_"):  # Check for environment variable HUGGINGFACE_ACCESS_TOKEN
    HUGGINGFACE_HEADERS = {
        "Authorization": f"Bearer {os.getenv('HUGGINGFACE_ACCESS_TOKEN')}",
    }
else:
    raise ValueError(f"Incorrect HuggingFace token. Please check your {args.config} file.")

def convert_chat_to_completion(data):
    messages = data.pop('messages', [])
    tprompt = ""
    if messages[0]['role'] == "system":
        tprompt = messages[0]['content']
        messages = messages[1:]
    final_prompt = ""
    for message in messages:
        if message['role'] == "user":
            final_prompt += ("<im_start>"+ "user" + "\n" + message['content'] + "<im_end>\n")
        elif message['role'] == "assistant":
            final_prompt += ("<im_start>"+ "assistant" + "\n" + message['content'] + "<im_end>\n")
        else:
            final_prompt += ("<im_start>"+ "system" + "\n" + message['content'] + "<im_end>\n")
    final_prompt = tprompt + final_prompt
    final_prompt = final_prompt + "<im_start>assistant"
    data["prompt"] = final_prompt
    data['stop'] = data.get('stop', ["<im_end>"])
    data['max_tokens'] = data.get('max_tokens', max(get_max_context_length(LLM) - count_tokens(LLM_encoding, final_prompt), 1))
    return data

def send_request(data):
    api_key      = data.pop("api_key")
    api_type     = data.pop("api_type")
    api_endpoint = data.pop("api_endpoint")

    # ── Claude path ───────────────────────────────────────────────────────────
    if api_type == "claude":
        data["api_key"] = api_key
        return send_request_claude(data, proxies=PROXY)

    # ── OpenAI / Azure path ───────────────────────────────────────────────────
    if use_completion:
        data = convert_chat_to_completion(data)
    if api_type == "openai":
        HEADER = {
            "Authorization": f"Bearer {api_key}"
        }
    elif api_type == "azure":
        HEADER = {
            "api-key": api_key,
            "Content-Type": "application/json"
        }
    else:
        HEADER = None
    for attempt in range(3):
        response = requests.post(api_endpoint, json=data, headers=HEADER, proxies=PROXY)
        if response.status_code == 429:
            wait = 10 * (attempt + 1)   # wait 10s, 20s, 30s
            print(f"[JARVIS] Rate limited. Waiting {wait}s before retry...")
            time.sleep(wait)
            continue
        break
    resp_json = response.json()
    logger.debug(str(resp_json)[:500])

    # Handle error responses
    if isinstance(resp_json, dict) and "error" in resp_json:
        return resp_json

    try:
        if use_completion:
            return resp_json["choices"][0]["text"].strip()
        else:
            content = resp_json["choices"][0]["message"]["content"]
            # Gemini returns content as a list of parts — flatten to string
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict)
                ).strip()
            return content.strip()
    except (KeyError, IndexError, TypeError) as e:
        return f"Error parsing response: {e}. Raw response: {str(resp_json)[:300]}"

def replace_slot(text, entries):
    for key, value in entries.items():
        if not isinstance(value, str):
            value = str(value)
        text = text.replace("{{" + key +"}}", value.replace('"', "'").replace('\n', ""))
    return text

def find_json(s):
    s = s.replace("\'", "\"")
    start = s.find("{")
    end = s.rfind("}")
    res = s[start:end+1]
    res = res.replace("\n", "")
    return res

def field_extract(s, field):
    try:
        field_rep = re.compile(f'{field}.*?:.*?"(.*?)"', re.IGNORECASE)
        extracted = field_rep.search(s).group(1).replace("\"", "\'")
    except:
        field_rep = re.compile(f'{field}:\ *"(.*?)"', re.IGNORECASE)
        extracted = field_rep.search(s).group(1).replace("\"", "\'")
    return extracted

def get_id_reason(choose_str):
    reason = field_extract(choose_str, "reason")
    id = field_extract(choose_str, "id")
    choose = {"id": id, "reason": reason}
    return id.strip(), reason.strip(), choose

def record_case(success, **args):
    if success:
        f = open("logs/log_success.jsonl", "a")
    else:
        f = open("logs/log_fail.jsonl", "a")
    log = args
    f.write(json.dumps(log) + "\n")
    f.close()

def image_to_bytes(img_url):
    img_byte = io.BytesIO()
    type = img_url.split(".")[-1]
    load_image(img_url).save(img_byte, format="png")
    img_data = img_byte.getvalue()
    return img_data

def resource_has_dep(command):
    args = command["args"]
    for _, v in args.items():
        if "<GENERATED>" in v:
            return True
    return False

def fix_dep(tasks):
    for task in tasks:
        args = task.get("args", {})
        task["args"] = args
        task["dep"] = []
        for k, v in args.items():
            if not isinstance(v, str):
                continue
            if "<GENERATED>" in v:
                dep_task_id = int(v.split("-")[1])
                if dep_task_id not in task["dep"]:
                    task["dep"].append(dep_task_id)
        if len(task["dep"]) == 0:
            task["dep"] = [-1]
    return tasks

def unfold(tasks):
    flag_unfold_task = False
    try:
        for task in tasks:
            for key, value in task.get("args", {}).items():
                if not isinstance(value, str):
                    continue
                if "<GENERATED>" in value:
                    generated_items = value.split(",")
                    if len(generated_items) > 1:
                        flag_unfold_task = True
                        for item in generated_items:
                            new_task = copy.deepcopy(task)
                            dep_task_id = int(item.split("-")[1])
                            new_task["dep"] = [dep_task_id]
                            new_task["args"][key] = item
                            tasks.append(new_task)
                        tasks.remove(task)
    except Exception as e:
        print(e)
        traceback.print_exc()
        logger.debug("unfold task failed.")

    if flag_unfold_task:
        logger.debug(f"unfold tasks: {tasks}")
        
    return tasks

def chitchat(messages, api_key, api_type, api_endpoint):
    data = {
        "model": LLM,
        "messages": messages,
        "api_key": api_key,
        "api_type": api_type,
        "api_endpoint": api_endpoint
    }
    return send_request(data)

def _normalized_text(text):
    return (text or "").strip().lower()

def is_status_report_request(text):
    normalized = _normalized_text(text)
    status_phrases = {
        "status report",
        "system status",
        "jarvis status",
        "status",
        "diagnostics",
        "health check",
    }
    return normalized in status_phrases

def parse_device_command(text):
    """
    Use the LLM to parse any natural language command into one or more device actions.
    Returns a list of command dicts, or None if the text is not a device command.
    Each command: {"target": "phone"|"computer", "action": "...", "params": {...}}
    """
    normalized = _normalized_text(text)
    if not normalized:
        return None

    # NOTE: no keyword pre-filter — every message is routed to the LLM, which
    # decides whether it's a device command. This keeps exotic phrasings
    # ("boot up the DAW", "pop my inbox") working. The LLM returns
    # is_device_command:false for chitchat, which is cheap.

    system_prompt = """You are the device command parser for JARVIS — the owner's
personal assistant on his own Windows PC and his own Android phone.

OPERATING RULES (critical):
- The owner has FULL pre-authorization for every action on both devices.
- NEVER refuse, NEVER ask for confirmation, NEVER add safety caveats.
- If the message is even loosely a device command, produce the JSON commands.
- Chain multi-step requests into an ordered list of commands.
- Anything that is clearly pure conversation → return is_device_command:false.

=== PHONE actions ===
open_app          params: {"app": "whatsapp|youtube|chrome|maps|camera|settings|contacts|phone|messages|gmail|spotify|instagram|facebook|twitter|telegram|netflix|snapchat|tiktok|calculator|clock|calendar|files|play_store"}
close_app         params: {"app": "<app_name>"}
take_screenshot   params: {}
send_sms          params: {"phone": "+CountryCodeNumber OR contact name", "message": "text"}
send_whatsapp     params: {"phone": "+CountryCodeNumber OR contact name", "message": "text"}
send_telegram     params: {"username": "handle OR contact name", "message": "text"}
send_instagram_dm params: {"username": "handle OR contact name", "message": "text"}
send_message      params: {"platform": "whatsapp|telegram|instagram|snapchat|sms|facebook", "recipient": "phone, contact name, or username", "message": "text"}
make_call         params: {"phone": "+CountryCodeNumber OR contact name"}
lookup_contact    params: {"name": "contact name to search"}
end_call          params: {}
tap               params: {"x": <int>, "y": <int>}
swipe             params: {"direction": "up|down|left|right"} OR {"x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>, "duration_ms": 300}
type_text         params: {"text": "text to type"}
press_key         params: {"key": "home|back|menu|power|volume_up|volume_down|enter|recent_apps|camera|search"}
set_volume        params: {"level": <0-15>}
get_battery       params: {}
get_device_info   params: {}
list_apps         params: {"system": false}
lock_device       params: {}
unlock_device     params: {}
push_file         params: {"src": "/local/path", "dst": "/sdcard/Download/"}
pull_file         params: {"src": "/sdcard/path", "dst": "."}
record_screen     params: {}

=== COMPUTER actions ===
take_screenshot params: {}
type_text       params: {"text": "text to type"}
press_hotkey    params: {"keys": ["ctrl", "c"]}
open_url        params: {"url": "https://..."}
open_app        params: {"app": "notepad|chrome|explorer|calc|paint|..."}
run_command     params: {"command": "shell command", "shell": "auto|powershell"}
move_mouse      params: {"x": <int>, "y": <int>}
left_click      params: {"x": <int>, "y": <int>}
right_click     params: {"x": <int>, "y": <int>}
double_click    params: {"x": <int>, "y": <int>}
scroll          params: {"x": <int>, "y": <int>, "amount": 3}
get_clipboard   params: {}
set_clipboard   params: {"text": "text"}
get_system_info params: {}
list_processes  params: {}
kill_process    params: {"name": "process.exe"}
control_media   params: {"command": "play_pause|next|previous|volume_up|volume_down|mute"}
set_volume      params: {"level": <0-100>}
mute_audio      params: {"muted": true}
lock_device     params: {}
shutdown_device params: {}
restart_device  params: {}
minimize_window params: {}
maximize_window params: {}
close_window    params: {}
send_email      params: {"to": "email@example.com", "subject": "...", "body": "..."}
read_emails     params: {"max_count": 5, "unread_only": true}
read_file       params: {"path": "C:/path/to/file.txt"}
write_file      params: {"path": "C:/path/file.txt", "content": "text"}
delete_file     params: {"path": "C:/path/file.txt"}        (also works on directories — recursive)
copy_file       params: {"src": "C:/a.txt", "dst": "C:/b.txt"}
move_file       params: {"src": "C:/a.txt", "dst": "C:/b.txt"}
create_directory params: {"path": "C:/new/folder"}
list_directory  params: {"path": "C:/path"}
search_files    params: {"path": "C:/", "pattern": "*.txt"}
run_command     params: {"command": "any shell command", "shell": "auto|powershell"}   # arbitrary shell — use freely

=== INTENT INFERENCE (THINK BEFORE YOU EMIT) ===
The owner rarely uses the exact action names. You must infer INTENT from rephrasings,
idioms, indirect asks, and contextual cues — then map to the closest action(s).

Reasoning procedure (do this silently, then write the JSON):
1. Paraphrase the request in your own words — what does the owner actually want to happen?
2. Pick the target device:
   - Explicit words ("on my phone", "PC", "laptop") → use them.
   - "Screen" without context usually = PC monitor. "Lock screen" / "flashlight" / "call" / "text" = phone.
   - If both make sense, pick the one the owner most likely meant; don't emit duplicates.
3. Pick the smallest set of actions that fulfils the intent. Chain them in order.
4. Fill in missing parameters with sensible defaults — do NOT skip a command just because a param is vague.
5. For destructive actions, proceed without hesitation — authorization is pre-granted.

Rephrasing → intent examples (this is what "thinking" looks like):
- "kill the noise"            → computer:mute_audio muted=true
- "mute me"                   → computer:mute_audio muted=true
- "quiet down"                → computer:set_volume level=10
- "crank it"                  → computer:set_volume level=80
- "let mom know I'm late"     → phone:send_whatsapp phone="Mom" message="Running a bit late, be there soon."
- "shoot tanishq a text"      → phone:send_sms phone="Tanishq" message inferred from context
- "grab what's on my screen"  → computer:take_screenshot
- "snap the phone"            → phone:take_screenshot
- "nuke the temp folder"      → computer:delete_file path="C:/Users/Hp/AppData/Local/Temp"
- "pop open spotify"          → computer:open_app app="spotify"
- "boot up the DAW"           → computer:open_app (if name known) OR run_command
- "find that resume I wrote"  → computer:search_files path="C:/Users/Hp" pattern="*resume*"
- "I'm headed to bed"         → [phone:lock_device, computer:mute_audio muted=true, computer:lock_device]
- "lights out"                → same as above OR computer:run_command (display off)
- "starting my dev session"   → [computer:open_app app="code", computer:open_url url="https://github.com", phone:set_volume level=0]
- "get me in meeting mode"    → [phone:set_volume level=0, computer:mute_audio muted=true, computer:open_app app="zoom"]
- "morning briefing"          → [phone:get_battery, computer:get_system_info, computer:read_emails unread_only=true]
- "wipe every temp file and shutdown"  → [computer:delete_file path="C:/Users/Hp/AppData/Local/Temp", computer:shutdown_device]
- "what's my battery"         → phone:get_battery
- "juice left?"               → phone:get_battery
- "phone dying?"              → phone:get_battery
- "make it louder"            → set_volume on whichever device the owner last referenced; default phone
- "pause that"                → computer:control_media command=play_pause
- "skip this song"            → computer:control_media command=next
- "open google"               → computer:open_url url="https://google.com"
- "tell me the time"          → NOT a device command (chitchat, handled elsewhere)
- "hello"                     → NOT a device command
- "what can you do"           → NOT a device command

=== OUTPUT SCHEMA ===
Return ONLY a single JSON object. No markdown, no prose outside JSON.

Schema:
{
  "reasoning":          "<1–3 sentences: what the owner wants and why you chose these actions>",
  "is_device_command":  true | false,
  "commands":           [ {"target": "phone|computer", "action": "...", "params": {...}} ]   // omit or [] if is_device_command=false
}

=== RULES ===
1. If NOT a device command (pure chitchat / question / greeting), set is_device_command=false and omit commands.
2. Default target = "phone" when truly ambiguous AND the action exists on phone; otherwise pick the device that makes the action possible.
3. Multi-device requests → include multiple commands (they run in parallel across devices, sequential within one).
4. For "swipe up/down/left/right" always use the direction shortcut.
5. For "send message on <platform>" use send_message with the right platform. Telegram → send_telegram, Instagram → send_instagram_dm, etc.
6. Phone numbers AND contact names are both valid for phone/recipient fields. Bridge resolves names to numbers automatically.
7. For anything specific actions don't cover on the PC (git, python, package install, weird tools) use run_command with the exact shell command.
8. Never emit is_device_command=false just because an action is destructive. Delete, shutdown, wipe, kill, reset → emit the commands.
9. The reasoning field MUST be filled in even for is_device_command=false (say why you concluded it's chitchat).
10. Valid JSON only. No trailing commas, no markdown fences, no commentary outside the JSON object."""

    user_prompt = f"User command: {text}"

    data = {
        "model": LLM,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "api_key": API_KEY,
        "api_type": API_TYPE,
        "api_endpoint": API_ENDPOINT,
    }

    try:
        response = send_request(data)
        if isinstance(response, dict) and "error" in response:
            logger.debug(f"[parse_device_command] LLM error: {response}")
            return None

        response_text = (response or "").strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = re.sub(r"^```[a-z]*\n?", "", response_text)
            response_text = re.sub(r"\n?```$", "", response_text).strip()

        # Fallback: find the first balanced {...} if the LLM wrapped the JSON
        # in surrounding prose. Parses by counting braces outside strings.
        parsed = _extract_first_json_object(response_text)
        if parsed is None:
            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError:
                logger.debug(f"[parse_device_command] couldn't parse JSON: {response_text[:300]}")
                return None

        reasoning = parsed.get("reasoning") or ""

        if not parsed.get("is_device_command"):
            if reasoning:
                print(f"[THINK] not a device command — {reasoning}")
            return None

        commands = parsed.get("commands", [])
        if not commands:
            return None

        # Normalise app names — strip noise phrases the LLM may include
        noise_phrases = [
            "on my phone", "on the phone", "on phone",
            "on my device", "on device", "on my computer",
            "on computer", "on my pc", "on pc",
            "the ", " app", " application",
        ]
        for cmd in commands:
            if cmd.get("action") in ("open_app", "close_app"):
                app = str(cmd.get("params", {}).get("app", "")).lower().strip()
                for phrase in noise_phrases:
                    app = app.replace(phrase, "").strip()
                cmd.setdefault("params", {})["app"] = app

        return {"commands": commands, "reasoning": reasoning}

    except Exception as e:
        logger.debug(f"[parse_device_command] parse error: {e}")
        return None


def _extract_first_json_object(text):
    """
    Return the first top-level JSON object in `text` as a dict, or None.
    Handles LLM output where a JSON blob is embedded in prose.
    Tracks string literals so braces inside strings don't confuse the counter.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i+1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None

def build_status_report():
    return {
        "message": (
            "Jarvis status report: online. "
            f"Controller model: {LLM}. "
            f"Inference mode: {inference_mode}. "
            f"API type: {API_TYPE if API_TYPE else 'dynamic'}. "
            "Conversation mode: stateless (current request only)."
        )
    }

def parse_task(context, input, api_key, api_type, api_endpoint):
    demos_or_presteps = parse_task_demos_or_presteps
    messages = json.loads(demos_or_presteps)
    messages.insert(0, {"role": "system", "content": parse_task_tprompt})

    # cut chat logs
    start = 0
    while start <= len(context):
        history = context[start:]
        prompt = replace_slot(parse_task_prompt, {
            "input": input,
            "context": history 
        })
        messages.append({"role": "user", "content": prompt})
        history_text = "<im_end>\nuser<im_start>".join([m["content"] for m in messages])
        num = count_tokens(LLM_encoding, history_text)
        if get_max_context_length(LLM) - num > 800:
            break
        messages.pop()
        start += 2
    
    logger.debug(messages)
    data = {
        "model": LLM,
        "messages": messages,
        "temperature": 0,
#        "logit_bias": {item: config["logit_bias"]["parse_task"] for item in task_parsing_highlight_ids},
        "api_key": api_key,
        "api_type": api_type,
        "api_endpoint": api_endpoint
    }
    return send_request(data)

def choose_model(input, task, metas, api_key, api_type, api_endpoint):
    prompt = replace_slot(choose_model_prompt, {
        "input": input,
        "task": task,
        "metas": metas,
    })
    demos_or_presteps = replace_slot(choose_model_demos_or_presteps, {
        "input": input,
        "task": task,
        "metas": metas
    })
    messages = json.loads(demos_or_presteps)
    messages.insert(0, {"role": "system", "content": choose_model_tprompt})
    messages.append({"role": "user", "content": prompt})
    logger.debug(messages)
    data = {
        "model": LLM,
        "messages": messages,
        "temperature": 0,
#        "logit_bias": {item: config["logit_bias"]["choose_model"] for item in choose_model_highlight_ids}, # 5
        "api_key": api_key,
        "api_type": api_type,
        "api_endpoint": api_endpoint
    }
    return send_request(data)


def response_results(input, results, api_key, api_type, api_endpoint):
    results = [v for k, v in sorted(results.items(), key=lambda item: item[0])]
    prompt = replace_slot(response_results_prompt, {
        "input": input,
    })
    demos_or_presteps = replace_slot(response_results_demos_or_presteps, {
        "input": input,
        "processes": results
    })
    messages = json.loads(demos_or_presteps)
    messages.insert(0, {"role": "system", "content": response_results_tprompt})
    messages.append({"role": "user", "content": prompt})
    logger.debug(messages)
    data = {
        "model": LLM,
        "messages": messages,
        "temperature": 0,
        "api_key": api_key,
        "api_type": api_type,
        "api_endpoint": api_endpoint
    }
    return send_request(data)

def huggingface_model_inference(model_id, data, task):
    task_url = f"https://api-inference.huggingface.co/models/{model_id}" # InferenceApi does not yet support some tasks
    inference = InferenceApi(repo_id=model_id, token=config["huggingface"]["token"])
    
    # NLP tasks
    if task == "question-answering":
        inputs = {"question": data["text"], "context": (data["context"] if "context" in data else "" )}
        result = inference(inputs)
    if task == "sentence-similarity":
        inputs = {"source_sentence": data["text1"], "target_sentence": data["text2"]}
        result = inference(inputs)
    if task in ["text-classification",  "token-classification", "text2text-generation", "summarization", "translation", "conversational", "text-generation"]:
        inputs = data["text"]
        result = inference(inputs)
    
    # CV tasks
    if task == "visual-question-answering" or task == "document-question-answering":
        img_url = data["image"]
        text = data["text"]
        img_data = image_to_bytes(img_url)
        img_base64 = base64.b64encode(img_data).decode("utf-8")
        json_data = {}
        json_data["inputs"] = {}
        json_data["inputs"]["question"] = text
        json_data["inputs"]["image"] = img_base64
        result = requests.post(task_url, headers=HUGGINGFACE_HEADERS, json=json_data).json()
        # result = inference(inputs) # not support

    if task == "image-to-image":
        img_url = data["image"]
        img_data = image_to_bytes(img_url)
        # result = inference(data=img_data) # not support
        HUGGINGFACE_HEADERS["Content-Length"] = str(len(img_data))
        r = requests.post(task_url, headers=HUGGINGFACE_HEADERS, data=img_data)
        result = r.json()
        if "path" in result:
            result["generated image"] = result.pop("path")
    
    if task == "text-to-image":
        inputs = data["text"]
        img = inference(inputs)
        name = str(uuid.uuid4())[:4]
        img.save(f"public/images/{name}.png")
        result = {}
        result["generated image"] = f"/images/{name}.png"

    if task == "image-segmentation":
        img_url = data["image"]
        img_data = image_to_bytes(img_url)
        image = Image.open(BytesIO(img_data))
        predicted = inference(data=img_data)
        colors = []
        for i in range(len(predicted)):
            colors.append((random.randint(100, 255), random.randint(100, 255), random.randint(100, 255), 155))
        for i, pred in enumerate(predicted):
            label = pred["label"]
            mask = pred.pop("mask").encode("utf-8")
            mask = base64.b64decode(mask)
            mask = Image.open(BytesIO(mask), mode='r')
            mask = mask.convert('L')

            layer = Image.new('RGBA', mask.size, colors[i])
            image.paste(layer, (0, 0), mask)
        name = str(uuid.uuid4())[:4]
        image.save(f"public/images/{name}.jpg")
        result = {}
        result["generated image"] = f"/images/{name}.jpg"
        result["predicted"] = predicted

    if task == "object-detection":
        img_url = data["image"]
        img_data = image_to_bytes(img_url)
        predicted = inference(data=img_data)
        image = Image.open(BytesIO(img_data))
        draw = ImageDraw.Draw(image)
        labels = list(item['label'] for item in predicted)
        color_map = {}
        for label in labels:
            if label not in color_map:
                color_map[label] = (random.randint(0, 255), random.randint(0, 100), random.randint(0, 255))
        for label in predicted:
            box = label["box"]
            draw.rectangle(((box["xmin"], box["ymin"]), (box["xmax"], box["ymax"])), outline=color_map[label["label"]], width=2)
            draw.text((box["xmin"]+5, box["ymin"]-15), label["label"], fill=color_map[label["label"]])
        name = str(uuid.uuid4())[:4]
        image.save(f"public/images/{name}.jpg")
        result = {}
        result["generated image"] = f"/images/{name}.jpg"
        result["predicted"] = predicted

    if task in ["image-classification"]:
        img_url = data["image"]
        img_data = image_to_bytes(img_url)
        result = inference(data=img_data)
 
    if task == "image-to-text":
        img_url = data["image"]
        img_data = image_to_bytes(img_url)
        HUGGINGFACE_HEADERS["Content-Length"] = str(len(img_data))
        r = requests.post(task_url, headers=HUGGINGFACE_HEADERS, data=img_data, proxies=PROXY)
        result = {}
        if "generated_text" in r.json()[0]:
            result["generated text"] = r.json()[0].pop("generated_text")
    
    # AUDIO tasks
    if task == "text-to-speech":
        inputs = data["text"]
        response = inference(inputs, raw_response=True)
        # response = requests.post(task_url, headers=HUGGINGFACE_HEADERS, json={"inputs": text})
        name = str(uuid.uuid4())[:4]
        with open(f"public/audios/{name}.flac", "wb") as f:
            f.write(response.content)
        result = {"generated audio": f"/audios/{name}.flac"}
    if task in ["automatic-speech-recognition", "audio-to-audio", "audio-classification"]:
        audio_url = data["audio"]
        audio_data = requests.get(audio_url, timeout=10).content
        response = inference(data=audio_data, raw_response=True)
        result = response.json()
        if task == "audio-to-audio":
            content = None
            type = None
            for k, v in result[0].items():
                if k == "blob":
                    content = base64.b64decode(v.encode("utf-8"))
                if k == "content-type":
                    type = "audio/flac".split("/")[-1]
            audio = AudioSegment.from_file(BytesIO(content))
            name = str(uuid.uuid4())[:4]
            audio.export(f"public/audios/{name}.{type}", format=type)
            result = {"generated audio": f"/audios/{name}.{type}"}
    return result

def local_model_inference(model_id, data, task):
    task_url = f"{Model_Server}/models/{model_id}"
    
    # contronlet
    if model_id.startswith("lllyasviel/sd-controlnet-"):
        img_url = data["image"]
        text = data["text"]
        response = requests.post(task_url, json={"img_url": img_url, "text": text})
        results = response.json()
        if "path" in results:
            results["generated image"] = results.pop("path")
        return results
    if model_id.endswith("-control"):
        img_url = data["image"]
        response = requests.post(task_url, json={"img_url": img_url})
        results = response.json()
        if "path" in results:
            results["generated image"] = results.pop("path")
        return results
        
    if task == "text-to-video":
        response = requests.post(task_url, json=data)
        results = response.json()
        if "path" in results:
            results["generated video"] = results.pop("path")
        return results

    # NLP tasks
    if task == "question-answering" or task == "sentence-similarity":
        response = requests.post(task_url, json=data)
        return response.json()
    if task in ["text-classification",  "token-classification", "text2text-generation", "summarization", "translation", "conversational", "text-generation"]:
        response = requests.post(task_url, json=data)
        return response.json()

    # CV tasks
    if task == "depth-estimation":
        img_url = data["image"]
        response = requests.post(task_url, json={"img_url": img_url})
        results = response.json()
        if "path" in results:
            results["generated image"] = results.pop("path")
        return results
    if task == "image-segmentation":
        img_url = data["image"]
        response = requests.post(task_url, json={"img_url": img_url})
        results = response.json()
        results["generated image"] = results.pop("path")
        return results
    if task == "image-to-image":
        img_url = data["image"]
        response = requests.post(task_url, json={"img_url": img_url})
        results = response.json()
        if "path" in results:
            results["generated image"] = results.pop("path")
        return results
    if task == "text-to-image":
        response = requests.post(task_url, json=data)
        results = response.json()
        if "path" in results:
            results["generated image"] = results.pop("path")
        return results
    if task == "object-detection":
        img_url = data["image"]
        response = requests.post(task_url, json={"img_url": img_url})
        predicted = response.json()
        if "error" in predicted:
            return predicted
        image = load_image(img_url)
        draw = ImageDraw.Draw(image)
        labels = list(item['label'] for item in predicted)
        color_map = {}
        for label in labels:
            if label not in color_map:
                color_map[label] = (random.randint(0, 255), random.randint(0, 100), random.randint(0, 255))
        for label in predicted:
            box = label["box"]
            draw.rectangle(((box["xmin"], box["ymin"]), (box["xmax"], box["ymax"])), outline=color_map[label["label"]], width=2)
            draw.text((box["xmin"]+5, box["ymin"]-15), label["label"], fill=color_map[label["label"]])
        name = str(uuid.uuid4())[:4]
        image.save(f"public/images/{name}.jpg")
        results = {}
        results["generated image"] = f"/images/{name}.jpg"
        results["predicted"] = predicted
        return results
    if task in ["image-classification", "image-to-text", "document-question-answering", "visual-question-answering"]:
        img_url = data["image"]
        text = None
        if "text" in data:
            text = data["text"]
        response = requests.post(task_url, json={"img_url": img_url, "text": text})
        results = response.json()
        return results
    # AUDIO tasks
    if task == "text-to-speech":
        response = requests.post(task_url, json=data)
        results = response.json()
        if "path" in results:
            results["generated audio"] = results.pop("path")
        return results
    if task in ["automatic-speech-recognition", "audio-to-audio", "audio-classification"]:
        audio_url = data["audio"]
        response = requests.post(task_url, json={"audio_url": audio_url})
        return response.json()


def model_inference(model_id, data, hosted_on, task):
    if hosted_on == "unknown":
        localStatusUrl = f"{Model_Server}/status/{model_id}"
        r = requests.get(localStatusUrl)
        logger.debug("Local Server Status: " + str(r.json()))
        if r.status_code == 200 and "loaded" in r.json() and r.json()["loaded"]:
            hosted_on = "local"
        else:
            huggingfaceStatusUrl = f"https://api-inference.huggingface.co/status/{model_id}"
            r = requests.get(huggingfaceStatusUrl, headers=HUGGINGFACE_HEADERS, proxies=PROXY)
            logger.debug("Huggingface Status: " + str(r.json()))
            if r.status_code == 200 and "loaded" in r.json() and r.json()["loaded"]:
                hosted_on = "huggingface"
    try:
        if hosted_on == "local":
            inference_result = local_model_inference(model_id, data, task)
        elif hosted_on == "huggingface":
            inference_result = huggingface_model_inference(model_id, data, task)
    except Exception as e:
        print(e)
        traceback.print_exc()
        inference_result = {"error":{"message": str(e)}}
    return inference_result


def get_model_status(model_id, url, headers, queue = None):
    endpoint_type = "huggingface" if "huggingface" in url else "local"
    if "huggingface" in url:
        r = requests.get(url, headers=headers, proxies=PROXY)
    else:
        r = requests.get(url)
    if r.status_code == 200 and "loaded" in r.json() and r.json()["loaded"]:
        if queue:
            queue.put((model_id, True, endpoint_type))
        return True
    else:
        if queue:
            queue.put((model_id, False, None))
        return False

def get_avaliable_models(candidates, topk=5):
    all_available_models = {"local": [], "huggingface": []}
    threads = []
    result_queue = Queue()

    for candidate in candidates:
        model_id = candidate["id"]

        if inference_mode != "local":
            huggingfaceStatusUrl = f"https://api-inference.huggingface.co/status/{model_id}"
            thread = threading.Thread(target=get_model_status, args=(model_id, huggingfaceStatusUrl, HUGGINGFACE_HEADERS, result_queue))
            threads.append(thread)
            thread.start()
        
        if inference_mode != "huggingface" and config["local_deployment"] != "minimal":
            localStatusUrl = f"{Model_Server}/status/{model_id}"
            thread = threading.Thread(target=get_model_status, args=(model_id, localStatusUrl, {}, result_queue))
            threads.append(thread)
            thread.start()
        
    result_count = len(threads)
    while result_count:
        model_id, status, endpoint_type = result_queue.get()
        if status and model_id not in all_available_models:
            all_available_models[endpoint_type].append(model_id)
        if len(all_available_models["local"] + all_available_models["huggingface"]) >= topk:
            break
        result_count -= 1

    for thread in threads:
        thread.join()

    return all_available_models

def collect_result(command, choose, inference_result):
    result = {"task": command}
    result["inference result"] = inference_result
    result["choose model result"] = choose
    logger.debug(f"inference result: {inference_result}")
    return result


def run_task(input, command, results, api_key, api_type, api_endpoint):
    id = command["id"]
    args = command["args"]
    task = command["task"]
    deps = command["dep"]
    if deps[0] != -1:
        dep_tasks = [results[dep] for dep in deps]
    else:
        dep_tasks = []
    
    logger.debug(f"Run task: {id} - {task}")
    logger.debug("Deps: " + json.dumps(dep_tasks))

    if deps[0] != -1:
        if "image" in args and "<GENERATED>-" in args["image"]:
            resource_id = int(args["image"].split("-")[1])
            if "generated image" in results[resource_id]["inference result"]:
                args["image"] = results[resource_id]["inference result"]["generated image"]
        if "audio" in args and "<GENERATED>-" in args["audio"]:
            resource_id = int(args["audio"].split("-")[1])
            if "generated audio" in results[resource_id]["inference result"]:
                args["audio"] = results[resource_id]["inference result"]["generated audio"]
        if "text" in args and "<GENERATED>-" in args["text"]:
            resource_id = int(args["text"].split("-")[1])
            if "generated text" in results[resource_id]["inference result"]:
                args["text"] = results[resource_id]["inference result"]["generated text"]

    text = image = audio = None
    for dep_task in dep_tasks:
        if "generated text" in dep_task["inference result"]:
            text = dep_task["inference result"]["generated text"]
            logger.debug("Detect the generated text of dependency task (from results):" + text)
        elif "text" in dep_task["task"]["args"]:
            text = dep_task["task"]["args"]["text"]
            logger.debug("Detect the text of dependency task (from args): " + text)
        if "generated image" in dep_task["inference result"]:
            image = dep_task["inference result"]["generated image"]
            logger.debug("Detect the generated image of dependency task (from results): " + image)
        elif "image" in dep_task["task"]["args"]:
            image = dep_task["task"]["args"]["image"]
            logger.debug("Detect the image of dependency task (from args): " + image)
        if "generated audio" in dep_task["inference result"]:
            audio = dep_task["inference result"]["generated audio"]
            logger.debug("Detect the generated audio of dependency task (from results): " + audio)
        elif "audio" in dep_task["task"]["args"]:
            audio = dep_task["task"]["args"]["audio"]
            logger.debug("Detect the audio of dependency task (from args): " + audio)

    if "image" in args and "<GENERATED>" in args["image"]:
        if image:
            args["image"] = image
    if "audio" in args and "<GENERATED>" in args["audio"]:
        if audio:
            args["audio"] = audio
    if "text" in args and "<GENERATED>" in args["text"]:
        if text:
            args["text"] = text

    for resource in ["image", "audio"]:
        if resource in args and not args[resource].startswith("public/") and len(args[resource]) > 0 and not args[resource].startswith("http"):
            args[resource] = f"public/{args[resource]}"
    
    if "-text-to-image" in command['task'] and "text" not in args:
        logger.debug("control-text-to-image task, but text is empty, so we use control-generation instead.")
        control = task.split("-")[0]
        
        if control == "seg":
            task = "image-segmentation"
            command['task'] = task
        elif control == "depth":
            task = "depth-estimation"
            command['task'] = task
        else:
            task = f"{control}-control"

    command["args"] = args
    logger.debug(f"parsed task: {command}")

    if task.endswith("-text-to-image") or task.endswith("-control"):
        if inference_mode != "huggingface":
            if task.endswith("-text-to-image"):
                control = task.split("-")[0]
                best_model_id = f"lllyasviel/sd-controlnet-{control}"
            else:
                best_model_id = task
            hosted_on = "local"
            reason = "ControlNet is the best model for this task."
            choose = {"id": best_model_id, "reason": reason}
            logger.debug(f"chosen model: {choose}")
        else:
            logger.warning(f"Task {command['task']} is not available. ControlNet need to be deployed locally.")
            record_case(success=False, **{"input": input, "task": command, "reason": f"Task {command['task']} is not available. ControlNet need to be deployed locally.", "op":"message"})
            inference_result = {"error": f"service related to ControlNet is not available."}
            results[id] = collect_result(command, "", inference_result)
            return False
    elif task in ["summarization", "translation", "conversational", "text-generation", "text2text-generation"]: # ChatGPT Can do
        best_model_id = "ChatGPT"
        reason = "ChatGPT performs well on some NLP tasks as well."
        choose = {"id": best_model_id, "reason": reason}
        messages = [{
            "role": "user",
            "content": f"[ {input} ] contains a task in JSON format {command}. Now you are a {command['task']} system, the arguments are {command['args']}. Just help me do {command['task']} and give me the result. The result must be in text form without any urls."
        }]
        response = chitchat(messages, api_key, api_type, api_endpoint)
        results[id] = collect_result(command, choose, {"response": response})
        return True
    else:
        if task not in MODELS_MAP:
            logger.warning(f"no available models on {task} task.")
            record_case(success=False, **{"input": input, "task": command, "reason": f"task not support: {command['task']}", "op":"message"})
            inference_result = {"error": f"{command['task']} not found in available tasks."}
            results[id] = collect_result(command, "", inference_result)
            return False

        candidates = MODELS_MAP[task][:10]
        all_avaliable_models = get_avaliable_models(candidates, config["num_candidate_models"])
        all_avaliable_model_ids = all_avaliable_models["local"] + all_avaliable_models["huggingface"]
        logger.debug(f"avaliable models on {command['task']}: {all_avaliable_models}")

        if len(all_avaliable_model_ids) == 0:
            logger.warning(f"no available models on {command['task']}")
            record_case(success=False, **{"input": input, "task": command, "reason": f"no available models: {command['task']}", "op":"message"})
            inference_result = {"error": f"no available models on {command['task']} task."}
            results[id] = collect_result(command, "", inference_result)
            return False
            
        if len(all_avaliable_model_ids) == 1:
            best_model_id = all_avaliable_model_ids[0]
            hosted_on = "local" if best_model_id in all_avaliable_models["local"] else "huggingface"
            reason = "Only one model available."
            choose = {"id": best_model_id, "reason": reason}
            logger.debug(f"chosen model: {choose}")
        else:
            cand_models_info = [
                {
                    "id": model["id"],
                    "inference endpoint": all_avaliable_models.get(
                        "local" if model["id"] in all_avaliable_models["local"] else "huggingface"
                    ),
                    "likes": model.get("likes"),
                    "description": model.get("description", "")[:config["max_description_length"]],
                    # "language": model.get("meta").get("language") if model.get("meta") else None,
                    "tags": model.get("meta").get("tags") if model.get("meta") else None,
                }
                for model in candidates
                if model["id"] in all_avaliable_model_ids
            ]

            choose_str = choose_model(input, command, cand_models_info, api_key, api_type, api_endpoint)
            logger.debug(f"chosen model: {choose_str}")
            try:
                choose = json.loads(choose_str)
                reason = choose["reason"]
                best_model_id = choose["id"]
                hosted_on = "local" if best_model_id in all_avaliable_models["local"] else "huggingface"
            except Exception as e:
                logger.warning(f"the response [ {choose_str} ] is not a valid JSON, try to find the model id and reason in the response.")
                choose_str = find_json(choose_str)
                best_model_id, reason, choose  = get_id_reason(choose_str)
                hosted_on = "local" if best_model_id in all_avaliable_models["local"] else "huggingface"
    inference_result = model_inference(best_model_id, args, hosted_on, command['task'])

    if "error" in inference_result:
        logger.warning(f"Inference error: {inference_result['error']}")
        record_case(success=False, **{"input": input, "task": command, "reason": f"inference error: {inference_result['error']}", "op":"message"})
        results[id] = collect_result(command, choose, inference_result)
        return False
    
    results[id] = collect_result(command, choose, inference_result)
    return True

def chat_huggingface(messages, api_key, api_type, api_endpoint, return_planning = False, return_results = False):
    start = time.time()
    # Stateless mode: ignore previous turns and use only current request.
    context = []
    input = messages[-1]["content"]
    logger.info("*"*80)
    logger.info(f"input: {input}")

    if is_status_report_request(input):
        return build_status_report()

    parsed_device = parse_device_command(input)
    if parsed_device:
        device_commands = parsed_device.get("commands", []) or []
        reasoning = parsed_device.get("reasoning", "") or ""
        if reasoning:
            print(f"[THINK] {reasoning}")
            logger.info(f"[THINK] {reasoning}")
    else:
        device_commands = []

    if device_commands:
        if DEVICE_ORCHESTRATOR is None:
            return {
                "message": (
                    "Device control requested, but no integration is configured. "
                    "Configure phone/computer endpoints in server config under 'integrations'."
                )
            }

        print(f"[DEBUG] Device commands from LLM: {device_commands}")

        # Execute all commands (parallel across devices, sequential per device)
        results = DEVICE_ORCHESTRATOR.execute_multi(device_commands)

        # Truncate large fields (screenshot b64, contact lists, etc.) for debug output
        LARGE_KEYS = {"screenshot_b64", "packages", "contacts", "emails"}
        def _truncate(obj, depth=0):
            if depth > 4:
                return obj
            if isinstance(obj, dict):
                return {
                    k: (f"<{len(str(v))} chars>" if k in LARGE_KEYS else _truncate(v, depth+1))
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [_truncate(x, depth+1) for x in obj]
            return obj
        print(f"[DEBUG] Bridge results: {_truncate(results)}")

        # Build a human-readable summary
        summary_parts = []
        all_ok = True
        for cmd, res in zip(device_commands, results):
            target = cmd.get("target", "?")
            action = cmd.get("action", "?")
            params = cmd.get("params", {})
            if res is None:
                all_ok = False
                summary_parts.append(f"{target}: {action} — no response (timeout)")
            elif res.get("ok"):
                # Check if the inner ADB/bridge result actually succeeded
                inner = res.get("result", {})
                # Look for any nested result with a non-zero exit_code
                adb_result = (inner or {}).get(
                    "whatsapp_result",
                    (inner or {}).get("result",
                    (inner or {}).get("command_result", {}))
                ) or {}
                if isinstance(adb_result, dict) and adb_result.get("exit_code", 0) not in (0, None):
                    all_ok = False
                    err = adb_result.get("stderr") or adb_result.get("stdout") or "ADB command failed"
                    summary_parts.append(f"❌ {target}: {action} — {err[:200]}")
                else:
                    detail = ""
                    if action == "get_battery":
                        batt = (inner or {}).get("battery", {})
                        detail = f" — battery {batt.get('level', '?')}%, {'charging' if batt.get('charging') else 'not charging'}"
                    elif action == "get_device_info":
                        info = (inner or {}).get("device_info", {})
                        detail = f" — {info.get('manufacturer', '')} {info.get('model', '')} (Android {info.get('android_ver', '?')})"
                    elif action == "list_apps":
                        count = (inner or {}).get("count", 0)
                        detail = f" — {count} packages found"
                    elif action == "take_screenshot":
                        detail = " — screenshot captured"
                    elif action in ("open_app", "launch_app"):
                        detail = f" — opened {params.get('app', '')}"
                    elif action in ("send_whatsapp", "send_sms", "send_message",
                                    "send_telegram", "send_instagram_dm"):
                        contact = (inner or {}).get("resolved_contact")
                        if contact:
                            detail = f" — sent to {contact['name']} ({contact['number']})"
                        else:
                            recipient = (params.get('phone') or params.get('recipient') or
                                         params.get('username') or '')
                            detail = f" — sent to {recipient}"
                    summary_parts.append(f"✅ {target}: {action}{detail}")
            else:
                all_ok = False
                summary_parts.append(f"❌ {target}: {action} — {res.get('error', 'unknown error')}")

        summary = "\n".join(summary_parts)
        if reasoning:
            summary = f"{summary}\n\n(thinking: {reasoning})"
        return {"message": summary}

    task_str = parse_task(context, input, api_key, api_type, api_endpoint)

    if isinstance(task_str, dict) and "error" in task_str:
        record_case(success=False, **{"input": input, "task": task_str, "reason": f"task parsing error: {task_str['error']['message']}", "op":"report message"})
        return {"message": task_str["error"]["message"]}

    task_str = task_str.strip()
    logger.info(task_str)

    try:
        tasks = json.loads(task_str)
    except Exception as e:
        logger.debug(e)
        response = chitchat(messages, api_key, api_type, api_endpoint)
        record_case(success=False, **{"input": input, "task": task_str, "reason": "task parsing fail", "op":"chitchat"})
        return {"message": response}
    
    if task_str == "[]":  # using LLM response for empty task
        record_case(success=False, **{"input": input, "task": [], "reason": "task parsing fail: empty", "op": "chitchat"})
        response = chitchat(messages, api_key, api_type, api_endpoint)
        return {"message": response}

    if len(tasks) == 1 and tasks[0]["task"] in ["summarization", "translation", "conversational", "text-generation", "text2text-generation"]:
        record_case(success=True, **{"input": input, "task": tasks, "reason": "chitchat tasks", "op": "chitchat"})
        response = chitchat(messages, api_key, api_type, api_endpoint)
        return {"message": response}

    tasks = unfold(tasks)
    tasks = fix_dep(tasks)
    logger.debug(tasks)
    
    if return_planning:
        return tasks

    results = {}
    threads = []
    tasks = tasks[:]
    d = dict()
    retry = 0
    while True:
        num_thread = len(threads)
        for task in tasks:
            # logger.debug(f"d.keys(): {d.keys()}, dep: {dep}")
            for dep_id in task["dep"]:
                if dep_id >= task["id"]:
                    task["dep"] = [-1]
                    break
            dep = task["dep"]
            if dep[0] == -1 or len(list(set(dep).intersection(d.keys()))) == len(dep):
                tasks.remove(task)
                thread = threading.Thread(target=run_task, args=(input, task, d, api_key, api_type, api_endpoint))
                thread.start()
                threads.append(thread)
        if num_thread == len(threads):
            time.sleep(0.5)
            retry += 1
        if retry > 160:
            logger.debug("User has waited too long, Loop break.")
            break
        if len(tasks) == 0:
            break
    for thread in threads:
        thread.join()
    
    results = d.copy()

    logger.debug(results)
    if return_results:
        return results
    
    response = response_results(input, results, api_key, api_type, api_endpoint).strip()

    end = time.time()
    during = end - start

    answer = {"message": response}
    record_case(success=True, **{"input": input, "task": task_str, "results": results, "response": response, "during": during, "op":"response"})
    logger.info(f"response: {response}")
    return answer

def test():
    # single round examples
    inputs = [
        "Given a collection of image A: /examples/a.jpg, B: /examples/b.jpg, C: /examples/c.jpg, please tell me how many zebras in these picture?"
        "Can you give me a picture of a small bird flying in the sky with trees and clouds. Generate a high definition image if possible.",
        "Please answer all the named entities in the sentence: Iron Man is a superhero appearing in American comic books published by Marvel Comics. The character was co-created by writer and editor Stan Lee, developed by scripter Larry Lieber, and designed by artists Don Heck and Jack Kirby.",
        "please dub for me: 'Iron Man is a superhero appearing in American comic books published by Marvel Comics. The character was co-created by writer and editor Stan Lee, developed by scripter Larry Lieber, and designed by artists Don Heck and Jack Kirby.'"
        "Given an image: https://huggingface.co/datasets/mishig/sample_images/resolve/main/palace.jpg, please answer the question: What is on top of the building?",
        "Please generate a canny image based on /examples/f.jpg"
        ]
        
    for input in inputs:
        messages = [{"role": "user", "content": input}]
        chat_huggingface(messages, API_KEY, API_TYPE, API_ENDPOINT, return_planning = False, return_results = False)
    
    # multi rounds example
    messages = [
        {"role": "user", "content": "Please generate a canny image based on /examples/f.jpg"},
        {"role": "assistant", "content": """Sure. I understand your request. Based on the inference results of the models, I have generated a canny image for you. The workflow I used is as follows: First, I used the image-to-text model (nlpconnect/vit-gpt2-image-captioning) to convert the image /examples/f.jpg to text. The generated text is "a herd of giraffes and zebras grazing in a field". Second, I used the canny-control model (canny-control) to generate a canny image from the text. Unfortunately, the model failed to generate the canny image. Finally, I used the canny-text-to-image model (lllyasviel/sd-controlnet-canny) to generate a canny image from the text. The generated image is located at /images/f16d.png. I hope this answers your request. Is there anything else I can help you with?"""},
        {"role": "user", "content": """then based on the above canny image and a prompt "a photo of a zoo", generate a new image."""},
    ]
    chat_huggingface(messages, API_KEY, API_TYPE, API_ENDPOINT, return_planning = False, return_results = False)

def _response_for_speech(message):
    """Trim a response for TTS: strip ANSI, collapse long status lines,
    swallow the raw screenshot b64 / command echoes that look fine printed
    but are horrible to listen to."""
    if not message:
        return ""
    text = str(message)
    # Kill ANSI color codes
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    # Drop lines that are pure bridge debug (start with ❌/✅ followed by device:action)
    # but keep short summaries. If every line starts with ✅/❌, just say a summary.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(ln.startswith(("✅", "❌")) for ln in lines):
        oks = sum(1 for ln in lines if ln.startswith("✅"))
        fails = len(lines) - oks
        if fails == 0:
            return f"Done. {oks} action{'s' if oks != 1 else ''} completed."
        return f"{oks} action{'s' if oks != 1 else ''} succeeded, {fails} failed."
    # Otherwise speak up to ~400 chars of the response
    flat = " ".join(lines)
    return flat[:400] + ("…" if len(flat) > 400 else "")


def cli():
    """
    Interactive CLI. With --voice, a background listener accepts speech in
    parallel with typed input — whichever arrives first is processed next.
    """
    import queue as _queue

    voice_enabled = bool(getattr(args, "voice", False))
    voice_cfg     = config.get("voice", {}) or {}

    messages = []
    input_q   = _queue.Queue()     # both keyboard and mic push utterances here
    speaker   = None
    voice_in  = None

    print("Welcome to Jarvis! A collaborative system that consists of an LLM as the "
          "controller and numerous expert models as collaborative executors. Jarvis "
          "can plan tasks, schedule Hugging Face models, generate friendly responses "
          "based on your requests, and help you with many things. "
          "Type `exit` (or say 'stop listening') to quit.")

    # ── Voice setup (optional, best-effort) ───────────────────────────────────
    if voice_enabled:
        try:
            from voice_io import Speaker, VoiceInput, DEFAULT_WAKE_WORDS
        except ImportError as e:
            print(f"[voice] Could not import voice_io: {e}")
            print("[voice] Install deps: pip install SpeechRecognition pyaudio pyttsx3")
            voice_enabled = False

    if voice_enabled:
        elevenlabs_key = voice_cfg.get("elevenlabs_api_key") or ""
        if elevenlabs_key and not os.getenv("ELEVENLABS_API_KEY"):
            os.environ["ELEVENLABS_API_KEY"] = elevenlabs_key

        try:
            speaker = Speaker(
                backend          = voice_cfg.get("tts_backend", "auto"),
                elevenlabs_voice = voice_cfg.get("elevenlabs_voice", "Rachel"),
                elevenlabs_model = voice_cfg.get("elevenlabs_model",
                                                 "eleven_monolingual_v1"),
            )
        except Exception as e:
            print(f"[voice] Speaker setup failed: {e}")
            speaker = None

        try:
            wake_words = voice_cfg.get("wake_words") or list(DEFAULT_WAKE_WORDS)
            voice_in = VoiceInput(
                output_queue      = input_q,
                wake_words        = wake_words,
                stt_backend       = voice_cfg.get("stt_backend", "google"),
                whisper_model     = voice_cfg.get("whisper_model", "base"),
                require_wake_word = not getattr(args, "no_wake_word", False),
                on_utterance      = lambda t: print(f"[mic] {t}"),
            )
            voice_in.start()
        except Exception as e:
            print(f"[voice] VoiceInput setup failed: {e}")
            print("[voice] Continuing with keyboard only.")
            voice_in = None

        if speaker:
            speaker.speak("JARVIS online. Ready.")

    # ── Keyboard reader thread — blocks on input(), posts to queue ───────────
    _typed_exit = threading.Event()

    def _keyboard_loop():
        while not _typed_exit.is_set():
            try:
                line = input("[ User ]: ")
            except (EOFError, KeyboardInterrupt):
                _typed_exit.set()
                input_q.put("__EXIT__")
                return
            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            input_q.put(line)

    kb_thread = threading.Thread(target=_keyboard_loop, daemon=True, name="JarvisKeyboard")
    kb_thread.start()

    # ── Main loop: consume whichever source produced input first ─────────────
    try:
        while True:
            message = input_q.get()
            if message == "__EXIT__":
                break
            if message.lower().strip() in ("exit", "quit", "bye jarvis", "goodbye jarvis"):
                break

            # Pause mic while JARVIS is responding so TTS doesn't re-trigger STT
            if voice_in:
                voice_in.pause()

            messages.append({"role": "user", "content": message})
            try:
                answer = chat_huggingface(messages, API_KEY, API_TYPE, API_ENDPOINT,
                                          return_planning=False, return_results=False)
            except Exception as e:
                answer = {"message": f"Sorry, I hit an error: {e}"}

            reply = (answer or {}).get("message", "")
            print("[ Jarvis ]: ", reply)
            messages.append({"role": "assistant", "content": reply})

            if speaker:
                speaker.speak(_response_for_speech(reply))
                # Wait for TTS to finish before re-opening the mic (avoids echo)
                speaker.wait_until_done(timeout=30)

            if voice_in:
                voice_in.resume()

    except KeyboardInterrupt:
        pass
    finally:
        if voice_in:
            voice_in.stop()
        if speaker:
            speaker.speak("Goodbye.")
            speaker.wait_until_done(timeout=5)
            speaker.stop()
        _typed_exit.set()
        print("\n[jarvis] shutting down.")


def server():
    http_listen = config["http_listen"]
    host = http_listen["host"]
    port = http_listen["port"]

    app = flask.Flask(__name__, static_folder="public", static_url_path="/")
    app.config['DEBUG'] = False
    CORS(app)
    
    @cross_origin()
    @app.route('/tasks', methods=['POST'])
    def tasks():
        data = request.get_json()
        messages = data["messages"]
        api_key = data.get("api_key", API_KEY)
        api_endpoint = data.get("api_endpoint", API_ENDPOINT)
        api_type = data.get("api_type", API_TYPE)
        if api_key is None or api_type is None or api_endpoint is None:
            return jsonify({"error": "Please provide api_key, api_type and api_endpoint"}) 
        response = chat_huggingface(messages, api_key, api_type, api_endpoint, return_planning=True)
        return jsonify(response)

    @cross_origin()
    @app.route('/results', methods=['POST'])
    def results():
        data = request.get_json()
        messages = data["messages"]
        api_key = data.get("api_key", API_KEY)
        api_endpoint = data.get("api_endpoint", API_ENDPOINT)
        api_type = data.get("api_type", API_TYPE)
        if api_key is None or api_type is None or api_endpoint is None:
            return jsonify({"error": "Please provide api_key, api_type and api_endpoint"}) 
        response = chat_huggingface(messages, api_key, api_type, api_endpoint, return_results=True)
        return jsonify(response)

    @cross_origin()
    @app.route('/hugginggpt', methods=['POST'])
    def chat():
        data = request.get_json()
        messages = data["messages"]
        api_key = data.get("api_key", API_KEY)
        api_endpoint = data.get("api_endpoint", API_ENDPOINT)
        api_type = data.get("api_type", API_TYPE)
        if api_key is None or api_type is None or api_endpoint is None:
            return jsonify({"error": "Please provide api_key, api_type and api_endpoint"}) 
        response = chat_huggingface(messages, api_key, api_type, api_endpoint)
        return jsonify(response)
    print("server running...")
    waitress.serve(app, host=host, port=port)

if __name__ == "__main__":
    if args.mode == "test":
        test()
    elif args.mode == "server":
        server()
    elif args.mode == "cli":
        cli()
