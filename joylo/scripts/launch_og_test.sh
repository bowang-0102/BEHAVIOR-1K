# Ensure Isaac Sim / GLFW creates a local GUI window. The default display on
# this machine is :0; preserve an explicitly configured DISPLAY when present.
export DISPLAY="${DISPLAY:-:0}"
export OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING

# Use the current user's X11 cookie when available. The LightDM authority
# database is root-owned and is only needed for an explicit xhost fallback.
if [[ -z "${XAUTHORITY:-}" && -r "${HOME}/.Xauthority" ]]; then
  export XAUTHORITY="${HOME}/.Xauthority"
fi

# If the user cookie is stale or unavailable, use the same LightDM/X11
# authorization path as the working MuJoCo launcher.
XHOST_GRANTED=0
LIGHTDM_XAUTH="/var/run/lightdm/root/:0"
if ! DISPLAY="$DISPLAY" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" xhost >/dev/null 2>&1; then
  CURRENT_USER="$(id -un)"
  sudo env DISPLAY="$DISPLAY" XAUTHORITY="$LIGHTDM_XAUTH" \
    xhost "+SI:localuser:${CURRENT_USER}" >/dev/null
  export XAUTHORITY=/dev/null
  XHOST_GRANTED=1
fi

cleanup_xhost() {
  if [[ "$XHOST_GRANTED" -eq 1 ]]; then
    sudo env DISPLAY="$DISPLAY" XAUTHORITY="$LIGHTDM_XAUTH" \
      xhost "-SI:localuser:$(id -un)" >/dev/null 2>&1 || true
  fi
}
trap cleanup_xhost EXIT

python joylo/scripts/launch_og.py \
  --robot r1pro \
  --robot-port 6001 \
  --task-name turning_on_radio \
  --recording-path outputs/hdf5/turning_on_radio.hdf5