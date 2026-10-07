#!/bin/sh
# Dump `upsc` as one Influx line for telegraf (stats role reads it via /hostfs/run).
# Keeps upsd on localhost. ups.status becomes the telegraf upsd plugin's status_flags bitmask.
out={{ nut_metrics_file }}
now=$(date +%s)
if data=$(upsc {{ nut_ups_name }}@localhost 2>/dev/null); then
  line=$(printf '%s\n' "$data" | awk -F': ' -v ups={{ nut_ups_name }} -v now="$now" '
    BEGIN { split("OL OB LB HB RB CHRG DISCHRG BYPASS CAL OFF OVER TRIM BOOST FSD", names, " ")
            for (i in names) bit[names[i]] = 2 ^ (i - 1) }
    $1 == "ups.status" { n = split($2, st, " "); for (i = 1; i <= n; i++) flags += bit[st[i]]; next }
    $1 ~ /^(battery|input|output|ups)\./ && $1 !~ /id$/ && $2 ~ /^-?[0-9]+(\.[0-9]+)?$/ {
      k = $1; gsub(/\./, "_", k); f = f "," k "=" $2 }
    END { printf "nut,ups=%s up=1i,ups_status_flags=%di,updated_seconds=%di%s\n", ups, flags, now, f }')
else
  line="nut,ups={{ nut_ups_name }} up=0i,updated_seconds=${now}i"
fi
printf '%s\n' "$line" > "$out.tmp" && mv "$out.tmp" "$out"
