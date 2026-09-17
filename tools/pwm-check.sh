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

SETTLE="${SETTLE:-8}"
HIGH=255
LOW=60

if [[ $EUID -ne 0 ]]; then
    echo "Needs root:  sudo $0"
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

# Which chip the driver thinks this is decides the register map it uses, so a
# misidentification shows up as writes that land somewhere unhelpful.
for module in it87 nct6775 nct6683; do
    params="/sys/module/$module/parameters"
    [[ -d "$params" ]] || continue
    echo "  $module parameters:"
    for param in "$params"/*; do
        printf '    %-24s %s\n' "$(basename "$param")" "$(cat "$param" 2>/dev/null || echo '?')"
    done
done
if command -v dmesg >/dev/null; then
    detection="$(dmesg 2>/dev/null | grep -iE 'it87|nct67' | tail -8)"
    [[ -n "$detection" ]] && { echo "  what the driver said when it loaded:"; echo "$detection" | sed 's/^/    /'; }

    # The same chip identified two different ways is the thing to catch here:
    # reloading the module can leave the super-I/O in a state where detection
    # picks the wrong ID, and the wrong ID means the wrong register map.
    conflicts="$(dmesg 2>/dev/null \
        | grep -oiE 'Found [A-Z0-9]+ chip at 0x[0-9a-f]+' \
        | awk '{ print $NF, $2 }' | sort -u \
        | awk '{ seen[$1] = seen[$1] " " $2; count[$1]++ }
               END { for (addr in count) if (count[addr] > 1)
                         print "    " addr " has been identified as:" seen[addr] }')"
    if [[ -n "$conflicts" ]]; then
        echo
        printf '  \033[31m%s\033[0m\n' "The driver has identified the same chip more than one way:"
        echo "$conflicts"
        echo "    Only the first, at boot, was made with the chip in a known state."
        echo "    Reboot and run this again before changing anything else: the"
        echo "    wrong ID means the wrong register map, which is enough on its"
        echo "    own to explain channels that will not respond."
    fi
fi
echo

# Two chips reporting the same readings is a sign they are the same source,
# which matters before blaming the driver.
echo "Every chip on this machine:"
printf '  %-28s %-16s %s\n' "path" "name" "pwm / fan / temp"
while IFS=$'\t' read -r path name pwms fans temps; do
    printf '  %-28s %-16s %s / %s / %s\n' "$path" "$name" "$pwms" "$fans" "$temps"
done < <(list_chips)

echo
echo "All fan readings right now:  $(show_fans)"
echo

STEPS="${STEPS:-0 51 102 153 204 255}"
CHANNELS=0
WORKING=0

for pwm in "${PWMS[@]}"; do
    channel="$(basename "$pwm")"
    enable_file="${pwm}_enable"
    mode_file="${pwm}_mode"

    echo "── $channel ──"
    printf '  before      : pwm=%s' "$(cat "$pwm" 2>/dev/null || echo ?)"
    [[ -e "$enable_file" ]] && printf ' enable=%s' "$(cat "$enable_file")"
    [[ -e "$mode_file" ]] && printf ' mode=%s (0=DC 1=PWM)' "$(cat "$mode_file")"
    printf '\n'

    manual=no
    if [[ -e "$enable_file" ]]; then
        if echo 1 > "$enable_file" 2>/dev/null && [[ "$(cat "$enable_file")" == "1" ]]; then
            manual=yes
            echo "  manual mode : accepted"
        else
            echo "  manual mode : REFUSED - wrote 1, chip says $(cat "$enable_file" 2>/dev/null)"
        fi
    else
        echo "  manual mode : no ${channel}_enable"
    fi

    if [[ "$manual" == no ]]; then
        echo "  skipping the sweep: without manual mode the chip is still in charge"
        echo
        continue
    fi

    # Sweep upwards, so a fan that had to be restarted is already turning by
    # the time the higher steps are measured.
    printf '  %-6s %s\n' "pwm" "fan readings after ${SETTLE}s"
    first_reading=""
    last_reading=""
    for value in $STEPS; do
        if ! echo "$value" > "$pwm" 2>/dev/null; then
            printf '  %-6s write failed\n' "$value"
            continue
        fi
        back="$(cat "$pwm" 2>/dev/null || echo ?)"
        sleep "$SETTLE"
        reading="$(show_fans)"
        [[ -z "$first_reading" ]] && first_reading="$reading"
        last_reading="$reading"
        if [[ "$back" == "$value" ]]; then
            printf '  %-6s %s\n' "$value" "$reading"
        else
            printf '  %-6s (chip says %s) %s\n' "$value" "$back" "$reading"
        fi
    done
    CHANNELS=$(( CHANNELS + 1 ))
    [[ "$first_reading" != "$last_reading" ]] && WORKING=$(( WORKING + 1 ))
    echo
done

echo "Read each channel's sweep:"
echo "  readings rise with the value   -> that channel works"
echo "  readings flat across the sweep -> the fan ignores PWM, which usually"
echo "                                    means a 3-pin fan on a header set to"
echo "                                    PWM mode, or something else driving it"
echo "  manual mode refused            -> the chip will not hand that channel"
echo "                                    over. When only some channels refuse,"
echo "                                    the driver has most likely identified"
echo "                                    the chip wrongly and is using the"
echo "                                    wrong register map."

echo
if (( CHANNELS > 0 && WORKING < CHANNELS )); then
    echo "$WORKING of $CHANNELS swept channel(s) changed anything."
fi
if (( CHANNELS > 1 && WORKING <= 1 )); then
    cat <<'ADVICE'

Almost nothing responded, which points at the driver rather than the board:
with the wrong register map, writes land on channels that are not there.

Before compiling anything, make the driver use the identity you know is right.
On Gigabyte boards it87 can report an IT8689E as an IT8628E, and the two have
different maps:

    lsmod | grep it87                    # a module, or built into the kernel?

  If it is a module:
    sudo modprobe -r it87
    sudo modprobe it87 force_id=0x8689   # use the ID dmesg showed at boot
    sudo ./tools/pwm-check.sh

  If it is built in, the same goes on the kernel command line:
    sudo grubby --update-kernel=ALL --args="it87.force_id=0x8689"

  To keep it across reboots once it works:
    echo "options it87 force_id=0x8689" | sudo tee /etc/modprobe.d/it87.conf

If forcing the right ID does not help either, the out-of-tree driver at
https://github.com/frankcrawford/it87 knows more board variants than the one
in the kernel.
ADVICE
fi
