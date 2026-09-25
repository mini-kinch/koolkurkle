#!/usr/bin/env bash
# path_a_status.sh — read-only Path A status (bash and zsh, set -u).
# Prints one PASS/WARN/FAIL line per check, then a summary.
# Exit 0 when no check is FAIL, 1 otherwise.
# No writes except stdout/stderr. No mail, file contents, or absolute paths.
set -u

PA_CURL_TIMEOUT=3
PA_ASK_MIN=10240
PA_K20_RE='ASK_MAIL_PASTE_K:-["'"'"']?20["'"'"']?([^0-9]|$)'
PA_K8_RE='ASK_MAIL_PASTE_K:-["'"'"']?8["'"'"']?([^0-9]|$)'

pa_pass=0
pa_warn=0
pa_fail=0
pa_checks=""
pa_no_probe=0
pa_json=0
pa_http_rc=0
pa_http_body=""
pa_models_up=0
pa_models_body=""
pa_have_py=0
pa_have_curl=0

usage() {
  printf '%s\n' \
    "usage: path_a_status.sh [--no-probe] [--json]" \
    "  Read-only Path A status. One PASS/WARN/FAIL line per check, then a summary." \
    "  Exit 0 if no FAIL, 1 otherwise. Writes nothing except stdout/stderr." \
    "  --no-probe  skip the mlx chat probe" \
    "  --json      print one JSON object" \
    "  Env: MAILARCHIVE RETRIEVE_URL QWEN_BASE_URL QWEN_PROBE_TIMEOUT OLLAMA_BASE_URL"
}

pa_add() {
  _name="$1"
  _state="$2"
  _detail="$3"
  _detail=$(printf '%s' "$_detail" | tr '\t\r\n' '   ')
  if [ -n "${MAILARCHIVE-}" ]; then
    case "$_detail" in
      *"$MAILARCHIVE"*) _detail="redacted" ;;
    esac
  fi
  if [ -n "${HOME-}" ]; then
    case "$_detail" in
      *"$HOME"*) _detail="redacted" ;;
    esac
  fi
  pa_checks="${pa_checks}${_name}"$'\t'"${_state}"$'\t'"${_detail}"$'\n'
  case "$_state" in
    PASS) pa_pass=$((pa_pass + 1)) ;;
    WARN) pa_warn=$((pa_warn + 1)) ;;
    FAIL) pa_fail=$((pa_fail + 1)) ;;
  esac
}

pa_trim_slash() {
  printf '%s' "${1%/}"
}

pa_curl_get() {
  pa_http_body=""
  pa_http_rc=0
  pa_http_body=$(curl -q -sS --noproxy '*' --proto '=http,https' \
    --connect-timeout "$1" --max-time "$1" -f "$2" 2>/dev/null) || pa_http_rc=$?
}

pa_curl_post() {
  pa_http_body=""
  pa_http_rc=0
  pa_http_body=$(printf '%s' "$3" | curl -q -sS --noproxy '*' --proto '=http,https' \
    --connect-timeout "$1" --max-time "$1" -f \
    -H "Content-Type: application/json" --data-binary @- "$2" 2>/dev/null) || pa_http_rc=$?
}

pa_check_retrieve() {
  if [ "$pa_have_curl" -ne 1 ]; then
    pa_add retrieve_health FAIL "curl missing"
    return
  fi
  pa_curl_get "$PA_CURL_TIMEOUT" "$pa_health_url"
  if [ "$pa_http_rc" -eq 0 ]; then
    pa_add retrieve_health PASS "/health reachable"
  else
    pa_add retrieve_health FAIL "/health unreachable"
  fi
}

pa_check_models() {
  pa_models_up=0
  pa_models_body=""
  if [ "$pa_have_curl" -ne 1 ]; then
    pa_add mlx_models FAIL "curl missing"
    return
  fi
  pa_curl_get "$PA_CURL_TIMEOUT" "$pa_models_url"
  if [ "$pa_http_rc" -eq 0 ]; then
    pa_models_up=1
    pa_models_body="$pa_http_body"
    pa_add mlx_models PASS "/v1/models reachable"
  else
    pa_add mlx_models FAIL "/v1/models unreachable"
  fi
}

pa_check_probe() {
  if [ "$pa_no_probe" -eq 1 ]; then
    return
  fi
  if [ "$pa_have_curl" -ne 1 ]; then
    pa_add mlx_probe FAIL "curl missing"
    return
  fi
  if [ "$pa_have_py" -ne 1 ]; then
    pa_add mlx_probe FAIL "python3 missing"
    return
  fi
  pa_model_id=$(printf '%s' "$pa_models_body" | python3 -c '
import json, sys
raw = sys.stdin.read()
mid = "mlx-community/Qwen3.8-27B-4bit"
try:
    data = json.loads(raw) if raw.strip() else {}
    arr = data.get("data") if isinstance(data, dict) else None
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        cand = arr[0].get("id")
        if isinstance(cand, str) and cand.strip():
            mid = cand.strip()
except Exception:
    pass
sys.stdout.write(mid)
') || pa_model_id="mlx-community/Qwen3.8-27B-4bit"
  if [ -z "${pa_model_id-}" ]; then
    pa_model_id="mlx-community/Qwen3.8-27B-4bit"
  fi
  pa_req=$(python3 -c '
import json, sys
model = sys.argv[1]
sys.stdout.write(json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "ok"}],
    "max_tokens": 1,
    "temperature": 0,
    "chat_template_kwargs": {"enable_thinking": False},
}, separators=(",", ":")))
' "$pa_model_id") || {
    pa_add mlx_probe FAIL "chat probe failed"
    pa_model_id=""
    pa_req=""
    return
  }
  pa_curl_post "$PA_PROBE_TIMEOUT" "$pa_chat_url" "$pa_req"
  pa_model_id=""
  pa_req=""
  if [ "$pa_http_rc" -eq 0 ]; then
    pa_add mlx_probe PASS "chat probe max_tokens=1 thinking=off"
  elif [ "$pa_http_rc" -eq 28 ] && [ "$pa_models_up" -eq 1 ]; then
    pa_add mlx_probe FAIL "chat probe timed out; generate wedged: qwen-chat-down.sh then qwen-chat-up.sh"
  else
    pa_add mlx_probe FAIL "chat probe failed"
  fi
}

pa_check_ollama() {
  if [ "$pa_have_curl" -ne 1 ]; then
    pa_add ollama FAIL "curl missing"
    return
  fi
  pa_curl_get "$PA_CURL_TIMEOUT" "$pa_ollama_url"
  if [ "$pa_http_rc" -eq 0 ] && [ "$pa_models_up" -eq 1 ]; then
    pa_add ollama WARN "up while :1234 up (RAM rule: stop Ollama while Qwen up)"
  elif [ "$pa_http_rc" -eq 0 ]; then
    pa_add ollama PASS "up; mlx :1234 not up"
  else
    pa_add ollama PASS "down"
  fi
}

pa_check_files() {
  _missing=""
  _notexec=""
  for _name in ask_mail_wire.sh ask_mail_paste.sh qwen_paste_chat.sh qwen_paste_chat_post.py ask_mail_paste_fmt.py; do
    _rel="scripts/${_name}"
    _full="${MAILARCHIVE}/scripts/${_name}"
    if [ ! -f "$_full" ]; then
      _missing="${_missing}${_missing:+,}${_rel}"
    elif [ ! -x "$_full" ]; then
      _notexec="${_notexec}${_notexec:+,}${_rel}"
    fi
  done
  if [ -z "$_missing" ] && [ -z "$_notexec" ]; then
    pa_add path_a_files PASS "present+executable"
    return
  fi
  _detail=""
  if [ -n "$_missing" ]; then
    _detail="missing:${_missing}"
  fi
  if [ -n "$_notexec" ]; then
    _detail="${_detail}${_detail:+ }not_executable:${_notexec}"
  fi
  pa_add path_a_files FAIL "$_detail"
}

pa_check_k() {
  _bad8=""
  _bad_other=""
  for _name in ask_mail_paste.sh ask_mail_wire.sh qwen_paste_chat.sh; do
    _rel="scripts/${_name}"
    _full="${MAILARCHIVE}/scripts/${_name}"
    if [ ! -f "$_full" ]; then
      _bad_other="${_bad_other}${_bad_other:+; }${_rel} missing"
      continue
    fi
    if grep -E -q "$PA_K8_RE" "$_full" 2>/dev/null; then
      _bad8="${_bad8}${_bad8:+; }${_rel} default 8"
      continue
    fi
    if ! grep -E -q "$PA_K20_RE" "$_full" 2>/dev/null; then
      _bad_other="${_bad_other}${_bad_other:+; }${_rel} default not 20"
    fi
  done
  if [ -n "$_bad8" ] || [ -n "$_bad_other" ]; then
    _detail="${_bad8}"
    if [ -n "$_bad_other" ]; then
      _detail="${_detail}${_detail:+; }${_bad_other}"
    fi
    pa_add paste_k FAIL "$_detail"
  else
    pa_add paste_k PASS "default 20"
  fi
}

pa_file_meta() {
  _file="$1"
  pa_meta_size=""
  pa_meta_hex=""
  if [ "$pa_have_py" -eq 1 ]; then
    _meta=$(python3 -c '
import hashlib, sys
p = sys.argv[1]
h = hashlib.sha256()
n = 0
with open(p, "rb") as fh:
    while True:
        blob = fh.read(65536)
        if not blob:
            break
        n += len(blob)
        h.update(blob)
sys.stdout.write("%d:%s" % (n, h.hexdigest()[:12]))
' "$_file") || return 1
  else
    pa_meta_size=$(wc -c < "$_file" | tr -d '[:space:]') || return 1
    if command -v sha256sum >/dev/null 2>&1; then
      pa_meta_hex=$(sha256sum -- "$_file" | awk '{print substr($1,1,12)}') || return 1
    elif command -v shasum >/dev/null 2>&1; then
      pa_meta_hex=$(shasum -a 256 -- "$_file" | awk '{print substr($1,1,12)}') || return 1
    else
      return 1
    fi
    _meta="${pa_meta_size}:${pa_meta_hex}"
  fi
  pa_meta_size="${_meta%%:*}"
  pa_meta_hex="${_meta##*:}"
  case "$pa_meta_size" in
    ""|*[!0-9]*) return 1 ;;
  esac
  case "$pa_meta_hex" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) return 1 ;;
  esac
  return 0
}

pa_check_ask() {
  _full="${MAILARCHIVE}/scripts/ask_mail.py"
  if [ ! -f "$_full" ]; then
    pa_add ask_mail_py FAIL "missing scripts/ask_mail.py"
    return
  fi
  if ! pa_file_meta "$_full"; then
    pa_add ask_mail_py FAIL "size or sha256 unavailable"
    return
  fi
  if [ "$pa_meta_size" -lt "$PA_ASK_MIN" ]; then
    pa_add ask_mail_py WARN "size=${pa_meta_size} sha256=${pa_meta_hex} <10KB possible MCP stub"
  else
    pa_add ask_mail_py PASS "size=${pa_meta_size} sha256=${pa_meta_hex}"
  fi
}

pa_print_human() {
  printf '%s' "$pa_checks" | while IFS="$(printf '\t')" read -r _name _state _detail; do
    if [ -n "${_name-}" ]; then
      printf '%s %s %s\n' "$_state" "$_name" "$_detail"
    fi
  done
  printf 'SUMMARY pass=%s warn=%s fail=%s\n' "$pa_pass" "$pa_warn" "$pa_fail"
}

pa_print_json() {
  printf '%s' "$pa_checks" | python3 -c '
import json, sys
checks = []
for line in sys.stdin.read().splitlines():
    if not line:
        continue
    name, state, detail = line.split("\t", 2)
    checks.append({"name": name, "status": state, "detail": detail})

def count(state):
    return sum(1 for item in checks if item["status"] == state)

obj = {
    "pass": count("PASS"),
    "warn": count("WARN"),
    "fail": count("FAIL"),
    "checks": checks,
}
sys.stdout.write(json.dumps(obj, indent=2) + "\n")
'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-probe) pa_no_probe=1 ;;
    --json) pa_json=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown flag: %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "${MAILARCHIVE-}" ]; then
  if [ -z "${HOME-}" ]; then
    printf '%s\n' "error: HOME is unset and MAILARCHIVE is unset" >&2
    exit 2
  fi
  MAILARCHIVE="${HOME}/MailArchive"
fi
MAILARCHIVE="${MAILARCHIVE%/}"

PA_RETRIEVE_URL="${RETRIEVE_URL:-http://127.0.0.1:8743}"
PA_QWEN_BASE_URL="${QWEN_BASE_URL:-http://127.0.0.1:1234/v1}"
PA_OLLAMA_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
PA_PROBE_TIMEOUT="${QWEN_PROBE_TIMEOUT:-60}"
case "$PA_PROBE_TIMEOUT" in
  ""|*[!0-9]*|0) PA_PROBE_TIMEOUT=60 ;;
esac

pa_retrieve_base=$(pa_trim_slash "$PA_RETRIEVE_URL")
case "$pa_retrieve_base" in
  */health) pa_health_url="$pa_retrieve_base" ;;
  *) pa_health_url="${pa_retrieve_base}/health" ;;
esac
pa_qwen_base=$(pa_trim_slash "$PA_QWEN_BASE_URL")
pa_models_url="${pa_qwen_base}/models"
pa_chat_url="${pa_qwen_base}/chat/completions"
pa_ollama_base=$(pa_trim_slash "$PA_OLLAMA_URL")
pa_ollama_url="${pa_ollama_base}/api/tags"

if command -v curl >/dev/null 2>&1; then
  pa_have_curl=1
fi
if command -v python3 >/dev/null 2>&1; then
  pa_have_py=1
fi

pa_check_retrieve
pa_check_models
pa_check_probe
pa_check_ollama
pa_check_files
pa_check_k
pa_check_ask

pa_http_body=""
pa_models_body=""

if [ "$pa_json" -eq 1 ]; then
  if [ "$pa_have_py" -ne 1 ]; then
    printf '%s\n' "error: python3 required for --json" >&2
    exit 2
  fi
  pa_print_json
else
  pa_print_human
fi

if [ "$pa_fail" -eq 0 ]; then
  exit 0
fi
exit 1
