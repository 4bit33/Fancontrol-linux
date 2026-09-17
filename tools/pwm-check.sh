#!/usr/bin/env bash
#
# Does this chip actually accept PWM writes, and do the fans obey them?
#
#   sudo ./tools/pwm-check.sh
#
# Three things can go wrong and they need different fixes, so this separates
# them instead of reporting one "it does not work":
#
#   the register refuses the value   -> the driver or the chip rejects the write
#   the register holds it, fans do not move
#                                    -> something else is driving the fans,
#                                       typically the board's embedded
#                                       controller re-asserting its own curve
#   the fans move                    -> control works
#
# Everything is put back as it was, including on Ctrl-C.
#
set -u

CHIP="${CHIP:-it8689}"
SETTLE="${SETTLE:-8}"
HIGH=255
LOW=60
HWMON_ROOT="${FANCONTROL_HWMON_ROOT:-/sys/class/hwmon}"

if [[ $EUID -ne 0 ]]; then
    echo "Needs root:  sudo $0"
    exit 1
fi

HWMON=""
for candidate in "$HWMON_ROOT"/hwmon*; do
    [[ -r "$candidate/name" ]] || continue
    [[ "$(cat "$candidate/name")" == "$CHIP" ]] && { HWMON="$candidate"; break; }
done
[[ -z "$HWMON" ]] && { echo "No chip called '$CHIP' found."; exit 1; }

mapfile -t PWMS < <(ls "$HWMON"/pwm[0-9] 2>/dev/null | grep -E 'pwm[0-9]+$' | sort -V)
mapfile -t FANS < <(ls "$HWMON"/fan[0-9]_input 2>/dev/null | sort -V)

DAEMON_WAS_RUNNING=no
if systemctl is-active --quiet fancontrold.service 2>/dev/null; then
    DAEMON_WAS_RUNNING=yes
    systemctl stop fancontrold.service
    echo "Stopped fancontrold for the duration."
fi

declare -A SAVED_PWM SAVED_ENABLE
for each in "${PWMS[@]}"; do
    SAVED_PWM[$each]="$(cat "$each" 2>/dev/null || true)"
    [[ -e "${each}_enable" ]] && SAVED_ENABLE[$each]="$(cat "${each}_enable" 2>/dev/null || true)"
done

restore() {
    local each
    echo
    echo "Putting everything back."
    for each in "${PWMS[@]}"; do
        [[ -n "${SAVED_PWM[$each]:-}" ]] && echo "${SAVED_PWM[$each]}" > "$each" 2>/dev/null
        [[ -n "${SAVED_ENABLE[$each]:-}" ]] && echo "${SAVED_ENABLE[$each]}" > "${each}_enable" 2>/dev/null
    done
    [[ "$DAEMON_WAS_RUNNING" == yes ]] && systemctl start fancontrold.service
}
trap restore EXIT INT TERM

show_fans() {
    local each out=""
    for each in "${FANS[@]}"; do
        out+="$(basename "$each" _input)=$(cat "$each" 2>/dev/null || echo ?) "
    done
    echo "$out"
}

echo
echo "Chip $CHIP at $HWMON"
echo "Kernel module: $(basename "$(readlink -f "$HWMON/device/driver" 2>/dev/null)" 2>/dev/null || echo unknown)"
echo

# Anything else claiming the same readings is worth knowing about.
echo "Other chips reporting temperatures:"
for candidate in "$HWMON_ROOT"/hwmon*; do
    [[ -r "$candidate/name" ]] || continue
    name="$(cat "$candidate/name")"
    temps="$(ls "$candidate"/temp[0-9]_input 2>/dev/null | wc -l)"
    (( temps > 0 )) && printf '  %-16s %s temperature(s)\n' "$name" "$temps"
done

echo
echo "All fan readings right now:  $(show_fans)"
echo

for pwm in "${PWMS[@]}"; do
    channel="$(basename "$pwm")"
    enable_file="${pwm}_enable"
    mode_file="${pwm}_mode"

    echo "── $channel ──"
    printf '  before      : pwm=%s' "$(cat "$pwm" 2>/dev/null || echo ?)"
    [[ -e "$enable_file" ]] && printf ' enable=%s' "$(cat "$enable_file")"
    [[ -e "$mode_file" ]] && printf ' mode=%s' "$(cat "$mode_file")"
    printf '\n'

    if [[ -e "$enable_file" ]]; then
        if echo 1 > "$enable_file" 2>/dev/null; then
            back="$(cat "$enable_file")"
            if [[ "$back" == "1" ]]; then
                echo "  manual mode : accepted"
            else
                echo "  manual mode : WROTE 1, CHIP SAYS $back  <- the chip refused"
            fi
        else
            echo "  manual mode : the write itself failed"
        fi
    else
        echo "  manual mode : no ${channel}_enable, cannot ask for manual control"
    fi

    for value in "$HIGH" "$LOW"; do
        if ! echo "$value" > "$pwm" 2>/dev/null; then
            echo "  write $value : FAILED"
            continue
        fi
        back="$(cat "$pwm" 2>/dev/null || echo ?)"
        sleep "$SETTLE"
        if [[ "$back" == "$value" ]]; then
            printf '  write %-3s   : held, fans now %s\n' "$value" "$(show_fans)"
        else
            printf '  write %-3s   : CHIP SAYS %s, fans now %s\n' "$value" "$back" "$(show_fans)"
        fi
    done
    echo
done

echo "Read the two 'write' lines for each channel:"
echo "  a value that is not held        -> the chip or driver rejects the write"
echo "  values held but fans unchanged  -> something else is driving the fans"
echo "  fans change between 255 and 60  -> that channel works"
