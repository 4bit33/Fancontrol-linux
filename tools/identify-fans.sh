#!/usr/bin/env bash
#
# Work out which PWM channel drives which fan, by watching the tachometers.
#
#   sudo ./tools/identify-fans.sh
#
# Raises one channel at a time while holding the others at a low baseline, and
# reports which tachometer reacted. That is an objective answer: no listening
# at an open case, and it finds fans whose header you cannot easily see.
#
# Every channel is put back exactly as it was at the end, including on Ctrl-C.
#
set -u

CHIP="${CHIP:-it8689}"
BASELINE="${BASELINE:-77}"    # about 30%, low but above where most fans stall
FULL=255
SETTLE="${SETTLE:-4}"         # seconds for a fan to reach its new speed
#: A tachometer has to move by more than this to count as having reacted.
THRESHOLD="${THRESHOLD:-120}"

if [[ $EUID -ne 0 ]]; then
    echo "Driving fans needs root:"
    echo "    sudo $0"
    exit 1
fi

# FANCONTROL_HWMON_ROOT points this at a simulated tree, the same way the
# daemon takes it, so the script can be exercised without real fans.
HWMON_ROOT="${FANCONTROL_HWMON_ROOT:-/sys/class/hwmon}"

HWMON=""
for candidate in "$HWMON_ROOT"/hwmon*; do
    [[ -r "$candidate/name" ]] || continue
    if [[ "$(cat "$candidate/name")" == "$CHIP" ]]; then
        HWMON="$candidate"
        break
    fi
done
if [[ -z "$HWMON" ]]; then
    echo "No hwmon chip called '$CHIP' was found. What is here:"
    for candidate in "$HWMON_ROOT"/hwmon*; do
        [[ -r "$candidate/name" ]] && echo "    $(cat "$candidate/name")  ($candidate)"
    done
    echo
    echo "Pick one with:  sudo CHIP=<name> $0"
    exit 1
fi

mapfile -t PWMS < <(ls "$HWMON"/pwm[0-9] 2>/dev/null | grep -E 'pwm[0-9]+$' | sort -V)
mapfile -t FANS < <(ls "$HWMON"/fan[0-9]_input 2>/dev/null | sort -V)
if [[ ${#PWMS[@]} -eq 0 ]]; then
    echo "$CHIP has no PWM outputs."
    exit 1
fi

DAEMON_WAS_RUNNING=no
if systemctl is-active --quiet fancontrold.service 2>/dev/null; then
    DAEMON_WAS_RUNNING=yes
    echo "Stopping fancontrold so it does not fight this."
    systemctl stop fancontrold.service
fi

# Remember everything before touching it.
declare -A SAVED_PWM SAVED_ENABLE
for pwm in "${PWMS[@]}"; do
    SAVED_PWM[$pwm]="$(cat "$pwm" 2>/dev/null || echo "")"
    [[ -w "${pwm}_enable" ]] && SAVED_ENABLE[$pwm]="$(cat "${pwm}_enable" 2>/dev/null || echo "")"
done

restore() {
    echo
    echo "Putting every channel back as it was."
    local each
    for each in "${PWMS[@]}"; do
        [[ -n "${SAVED_PWM[$each]:-}" ]] && echo "${SAVED_PWM[$each]}" > "$each" 2>/dev/null
        [[ -n "${SAVED_ENABLE[$each]:-}" ]] && echo "${SAVED_ENABLE[$each]}" > "${each}_enable" 2>/dev/null
    done
    if [[ "$DAEMON_WAS_RUNNING" == yes ]]; then
        systemctl start fancontrold.service
        echo "fancontrold started again."
    fi
}
trap restore EXIT INT TERM

read_fans() {
    local out=() each
    for each in "${FANS[@]}"; do
        out+=("$(cat "$each" 2>/dev/null || echo 0)")
    done
    echo "${out[@]}"
}

set_all_to_baseline() {
    # "local", and not named "pwm": the caller loops over $pwm, and reusing the
    # name here left it pointing at the last channel, so every round raised the
    # same output instead of the one under test.
    local each
    for each in "${PWMS[@]}"; do
        [[ -w "${each}_enable" ]] && echo 1 > "${each}_enable" 2>/dev/null
        echo "$BASELINE" > "$each" 2>/dev/null
    done
}

echo
echo "Chip $CHIP at $HWMON"
echo "${#PWMS[@]} PWM output(s), ${#FANS[@]} tachometer(s)"
echo "Holding everything at ${BASELINE}/255 and raising one channel at a time."
echo

declare -A FOUND
for pwm in "${PWMS[@]}"; do
    channel="$(basename "$pwm")"

    set_all_to_baseline
    sleep "$SETTLE"
    read -ra before <<< "$(read_fans)"

    echo "$FULL" > "$pwm" 2>/dev/null
    sleep "$SETTLE"
    read -ra after <<< "$(read_fans)"

    echo "$BASELINE" > "$pwm" 2>/dev/null

    reacted=""
    for index in "${!FANS[@]}"; do
        fan_name="$(basename "${FANS[$index]}" _input)"
        delta=$(( ${after[$index]:-0} - ${before[$index]:-0} ))
        if (( delta > THRESHOLD )); then
            reacted+="${reacted:+, }$fan_name ${before[$index]}→${after[$index]} rpm"
        fi
    done

    if [[ -n "$reacted" ]]; then
        printf '  \033[32m%-6s\033[0m drives %s\n' "$channel" "$reacted"
        FOUND[$channel]="$reacted"
    else
        printf '  \033[2m%-6s nothing reacted - header is probably empty\033[0m\n' "$channel"
    fi
done

echo
echo "Summary"
for pwm in "${PWMS[@]}"; do
    channel="$(basename "$pwm")"
    if [[ -n "${FOUND[$channel]:-}" ]]; then
        echo "    $channel  ->  ${FOUND[$channel]}"
    else
        echo "    $channel  ->  (empty)"
    fi
done
echo
echo "Now match each channel to the header it must be, by where the fan is."
echo "A channel whose tachometer moved but that you cannot place is worth"
echo "raising on its own again while you look."
