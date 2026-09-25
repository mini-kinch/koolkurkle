#!/bin/zsh
# install_path_a_wire_to_mailarchive.sh — copy Path A wire/paste into $MAILARCHIVE.
# Rem-safe: never copies or overwrites ask_mail.py. No network, no Keychain, no iCloud.
# After copy, paste/wire/qwen scripts must still default ASK_MAIL_PASTE_K to 20.
# Portable bash or zsh (set -euo pipefail). Lives in repo scripts/.
set -euo pipefail

DRY_RUN=0
REL_COUNT=0
ASK_PATH=""
ASK_SUM=""
DEST=""
REPO_ROOT=""

K20_RE='ASK_MAIL_PASTE_K:-["'"'"']?20["'"'"']?([^0-9]|$)'
K8_RE='ASK_MAIL_PASTE_K:-["'"'"']?8["'"'"']?([^0-9]|$)'

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*" >&2
}

usage() {
  printf '%s\n' \
    "usage: install_path_a_wire_to_mailarchive.sh [--dry-run]" \
    "  Copy Path A wire/paste scripts and docs/path-a into \$MAILARCHIVE" \
    "  (default \$HOME/MailArchive). Does not copy or overwrite ask_mail.py." \
    "  --dry-run  print planned copies; write nothing."
}

each_rel() {
  # Hardcoded Path A set. Do not append from the environment.
  while IFS= read -r rel || [ -n "${rel-}" ]; do
    [ -n "$rel" ] || continue
    "$1" "$rel"
  done <<'EOF'
scripts/ask_mail_wire.sh
scripts/ask_mail_paste.sh
scripts/qwen_paste_chat.sh
scripts/qwen_paste_chat_post.py
scripts/ask_mail_paste_fmt.py
docs/path-a/fts_caveat.md
docs/path-a/wire-readme.md
docs/path-a/mini-install.md
EOF
}

names_ask_mail_py() {
  printf '%s\n' "$1" | grep -E '(^|[^A-Za-z0-9_])ask_mail\.py([^A-Za-z0-9_]|$)' >/dev/null 2>&1
}

refuse_ask_mail_name() {
  if names_ask_mail_py "$1"; then
    die "error: refusing to copy or overwrite ask_mail.py ($1)"
  fi
}

refuse_env_override() {
  _refuse_env SCRIPT_NAME "${SCRIPT_NAME-}"
  _refuse_env SCRIPT_NAMES "${SCRIPT_NAMES-}"
  _refuse_env COPY_LIST "${COPY_LIST-}"
  _refuse_env COPY_FILES "${COPY_FILES-}"
  _refuse_env INSTALL_FILES "${INSTALL_FILES-}"
  _refuse_env EXTRA_FILES "${EXTRA_FILES-}"
  _refuse_env PATH_A_FILES "${PATH_A_FILES-}"
}

_refuse_env() {
  if names_ask_mail_py "$2"; then
    die "error: refusing env $1 that forces ask_mail.py into the copy list"
  fi
}

dest_for() {
  case "$1" in
    /*|*..*)
      die "error: refusing copy path $1"
      ;;
    scripts/*)
      printf '%s\n' "$DEST/scripts/${1#scripts/}"
      ;;
    docs/path-a/*)
      printf '%s\n' "$DEST/docs/path-a/${1#docs/path-a/}"
      ;;
    *)
      die "error: copy list path not allowed: $1"
      ;;
  esac
}

needs_exec() {
  case "$1" in
    scripts/ask_mail_wire.sh|scripts/ask_mail_paste.sh|scripts/qwen_paste_chat.sh|scripts/qwen_paste_chat_post.py|scripts/ask_mail_paste_fmt.py)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_k_script() {
  case "$1" in
    scripts/ask_mail_wire.sh|scripts/ask_mail_paste.sh|scripts/qwen_paste_chat.sh)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

assert_k20_file() {
  file="$1"
  if [ ! -f "$file" ]; then
    die "error: missing $file for ASK_MAIL_PASTE_K assert"
  fi
  if ! grep -E "$K20_RE" "$file" >/dev/null 2>&1; then
    die "error: $file missing ASK_MAIL_PASTE_K default 20"
  fi
  if grep -E "$K8_RE" "$file" >/dev/null 2>&1; then
    die "error: $file defaults ASK_MAIL_PASTE_K to 8"
  fi
}

validate_rel() {
  rel="$1"
  src="$REPO_ROOT/$rel"
  dest="$(dest_for "$rel")"
  refuse_ask_mail_name "$rel"
  refuse_ask_mail_name "$dest"
  case "$dest" in
    "$DEST"/*) ;;
    *) die "error: dest escapes MAILARCHIVE: $dest" ;;
  esac
  if [ ! -f "$src" ]; then
    die "error: missing source $src"
  fi
  repo_ask="$REPO_ROOT/scripts/ask_mail.py"
  if [ -e "$repo_ask" ] && [ "$src" -ef "$repo_ask" ]; then
    die "error: refusing to copy ask_mail.py (source $src is ask_mail.py)"
  fi
  if [ -e "$ASK_PATH" ] && [ -e "$dest" ] && [ "$dest" -ef "$ASK_PATH" ]; then
    die "error: refusing to overwrite ask_mail.py via $dest"
  fi
  if [ -L "$dest" ]; then
    target="$(readlink "$dest")"
    if names_ask_mail_py "$target"; then
      die "error: refusing to overwrite ask_mail.py via symlink $dest -> $target"
    fi
  fi
}

count_rel() {
  REL_COUNT=$((REL_COUNT + 1))
  refuse_ask_mail_name "$1"
}

assert_src_rel() {
  if is_k_script "$1"; then
    assert_k20_file "$REPO_ROOT/$1"
  fi
}

assert_dest_rel() {
  if is_k_script "$1"; then
    assert_k20_file "$(dest_for "$1")"
  fi
}

copy_rel() {
  rel="$1"
  src="$REPO_ROOT/$rel"
  dest="$(dest_for "$rel")"
  refuse_ask_mail_name "$rel"
  refuse_ask_mail_name "$dest"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: $rel -> $dest"
    if needs_exec "$rel"; then
      log "dry-run: chmod +x $dest"
    fi
    return 0
  fi
  log "copy $rel -> $dest"
  cp "$src" "$dest"
  if needs_exec "$rel"; then
    chmod +x "$dest"
    log "chmod +x $dest"
  fi
}

snapshot_ask() {
  ASK_PATH="$DEST/scripts/ask_mail.py"
  ASK_SUM=""
  if [ -e "$ASK_PATH" ] || [ -L "$ASK_PATH" ]; then
    ASK_SUM="$(cksum "$ASK_PATH")"
    log "leaving existing ask_mail.py untouched"
  fi
}

verify_ask_untouched() {
  if [ -z "$ASK_SUM" ]; then
    if [ -e "$ASK_PATH" ] || [ -L "$ASK_PATH" ]; then
      die "error: refusing to create ask_mail.py"
    fi
    return 0
  fi
  if [ ! -e "$ASK_PATH" ] && [ ! -L "$ASK_PATH" ]; then
    die "error: ask_mail.py disappeared"
  fi
  now="$(cksum "$ASK_PATH")"
  if [ "$now" != "$ASK_SUM" ]; then
    die "error: ask_mail.py was modified"
  fi
}

resolve_repo_root() {
  script_path="$0"
  case "$script_path" in
    */*) ;;
    *) script_path="./$script_path" ;;
  esac
  scripts_dir="$(cd "$(dirname "$script_path")" && pwd)"
  if [ "$(basename "$scripts_dir")" != "scripts" ]; then
    die "error: script must live in repo scripts/"
  fi
  REPO_ROOT="$(cd "$scripts_dir/.." && pwd)"
}

main() {
  if [ "$#" -gt 1 ]; then
    usage >&2
    exit 2
  fi
  if [ "$#" -eq 1 ]; then
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      ask_mail.py|scripts/ask_mail.py|*/ask_mail.py)
        die "error: refusing to copy ask_mail.py"
        ;;
      *)
        usage >&2
        exit 2
        ;;
    esac
  fi

  if [ -z "${HOME-}" ] && [ -z "${MAILARCHIVE-}" ]; then
    die "error: HOME is unset and MAILARCHIVE is unset"
  fi

  resolve_repo_root
  refuse_env_override

  DEST="${MAILARCHIVE:-$HOME/MailArchive}"
  DEST="${DEST%/}"
  if [ -z "$DEST" ] || [ "$DEST" = "/" ]; then
    die "error: refusing empty or root MAILARCHIVE"
  fi

  ASK_PATH="$DEST/scripts/ask_mail.py"
  each_rel validate_rel
  REL_COUNT=0
  each_rel count_rel
  if [ "$REL_COUNT" -ne 8 ]; then
    die "error: copy list must be exactly the Path A wire/paste set (got $REL_COUNT)"
  fi
  each_rel assert_src_rel
  snapshot_ask

  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: no writes"
    each_rel copy_rel
    verify_ask_untouched
    printf '%s\n' "dry-run ok: Path A wire/paste -> $DEST"
    exit 0
  fi

  mkdir -p "$DEST/scripts" "$DEST/docs/path-a"
  each_rel copy_rel
  each_rel assert_dest_rel
  verify_ask_untouched
  printf '%s\n' "installed Path A wire/paste -> $DEST"
}

main "$@"
