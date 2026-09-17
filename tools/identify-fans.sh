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

HWMON_ROOT="${FANCONTROL_HWMON_ROOT:-/sys/class/hwmon}"

# Find the chip to work on. A name can be given with CHIP=, but the default is
# simply "the one that has PWM outputs", because that is what we need and
# because a name that does not match is a dead end with nothing to go on.
list_chips() {
    local candidate name pwms temps fans
    for candidate in "$HWMON_ROOT"/hwmon*; do
        [[ -r "$candidate/name" ]] || continue
        name="$(cat "$candidate/name")"
        pwms=$(ls "$candidate"/pwm[0-9] 2>/dev/null | grep -cE 'pwm[0-9]+$' || true)
        fans=$(ls "$candidate"/fan[0-9]_input 2>/dev/null | wc -l)
        temps=$(ls "$candidate"/temp[0-9]_input 2>/dev/null | wc -l)
        printf '%s\t%s\t%s\t%s\t%s\n' "$candidate" "$name" "$pwms" "$fans" "$temps"
    done
}

find_chip() {
    local line path name pwms
    while IFS=$'\t' read -r path name pwms _ _; do
        [[ -n "${CHIP:-}" && "$name" != "$CHIP" ]] && continue
        (( pwms > 0 )) && { echo "$path"; return 0; }
    done < <(list_chips)
    return 1
}

if ! HWMON="$(find_chip)"; then
    echo
    if [[ -n "${CHIP:-}" ]]; then
        echo "No chip called '$CHIP' with PWM outputs was found."
    else
        echo "No hwmon chip with PWM outputs was found."
    fi
    echo
    printf '  %-28s %-16s %s\n' "path" "name" "pwm / fan / temp"
    while IFS=$'\t' read -r path name pwms fans temps; do
        printf '  %-28s %-16s %s / %s / %s\n' "$path" "$name" "$pwms" "$fans" "$temps"
    done < <(list_chips)
    echo
    echo "If the chip you expect is missing, its driver is not loaded:"
    echo "    sudo modprobe it87        # Gigabyte and other ITE boards"
    echo "    sudo modprobe nct6775     # Asus, MSI and others"
    echo "If it is listed but has no PWM, that driver exposes no fan control."
    exit 1
fi
CHIP="$(cat "$HWMON/name")"

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
