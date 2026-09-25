# Path A model pin

`scripts/qwen-chat-up.sh` starts `mlx_lm.server` on one snapshot:

`$HOME/.cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-4bit/snapshots/10c35caafbb80f7dc6a7a432cdd11af10a6d4818`

That path is the watchdog default `WD_MODEL_PATH`. The script sets `PIN` to it and sets `MOD` to the repo directory two levels above `PIN` (`models--mlx-community--Qwen3.8-27B-4bit`). It does not list the Hugging Face hub and does not pick a folder with a glob.

## Why

A second hub folder that matches `models--*Qwen*` can sort first. `models--aaa--Qwen-decoy` is one such decoy: a directory listing that takes the first match would start those weights and never reach the 27B snapshot. The pin ignores every other `models--*` directory.

The snapshot is ready only when `PIN` is a directory, `PIN` contains at least one `*.safetensors` file, and `MOD` contains zero `*.incomplete` files. Otherwise the script logs one fail-closed line naming `PIN` and the reason (`missing dir`, `no safetensors`, or `incomplete files`), does not write the mlx plist, does not call `launchctl` for `com.mailroom.mlx-lm-server`, and exits 3. The ask_mail FTS-only serve section earlier in the script still runs.

## Install (Mini operator)

From a checkout of this change, back up the live script, then copy the merged script over it.

```sh
cp "$HOME/MailArchive/scripts/qwen-chat-up.sh" "$HOME/MailArchive/scripts/qwen-chat-up.sh.bak"
cp scripts/qwen-chat-up.sh "$HOME/MailArchive/scripts/qwen-chat-up.sh"
```

(a) That copy is the install of `scripts/qwen-chat-up.sh` onto `$HOME/MailArchive/scripts/qwen-chat-up.sh`.

(b) Disable the hf-qwen-stage LaunchAgent persistently. Leave its plist on disk. Do not delete or move `$HOME/Library/LaunchAgents/com.mailroom.hf-qwen-stage.plist`.

```sh
launchctl bootout gui/$(id -u)/com.mailroom.hf-qwen-stage 2>/dev/null || true
launchctl disable gui/$(id -u)/com.mailroom.hf-qwen-stage
launchctl print-disabled gui/$(id -u) | grep hf-qwen-stage
```

The `grep` line shows `hf-qwen-stage` disabled. `launchctl disable` survives reboot and does not remove the plist.

(c) No restart is required. The running mlx server already uses the pinned path. The new script takes effect the next time `qwen-chat-up.sh` runs.

## Rollback

Restore the backed-up `qwen-chat-up.sh` over `$HOME/MailArchive/scripts/qwen-chat-up.sh`, then enable the stage agent again:

```sh
launchctl enable gui/$(id -u)/com.mailroom.hf-qwen-stage
```
