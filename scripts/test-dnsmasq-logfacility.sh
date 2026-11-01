#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
dnsmasq_bin="${DNSMASQ_BIN:-$(command -v dnsmasq)}"
test -x "$dnsmasq_bin"
helper="$PWD/package/emortal/default-settings/files/dnsmasq-logfacility"
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
mkdir "$test_dir/bin"
cat > "$test_dir/bin/uci" <<'EOF'
#!/bin/sh
[ "$1" = -q ] && [ "$2" = get ] || exit 91
case "$3" in
'dhcp.@dnsmasq[0]') echo dnsmasq ;;
'dhcp.@dnsmasq[1]') [ "${TEST_UCI_SECOND:-0}" = 1 ] || exit 1; echo dnsmasq ;;
'dhcp.@dnsmasq[0].logfacility') [ -n "$TEST_UCI_FACILITY" ] || exit 1; echo "$TEST_UCI_FACILITY" ;;
'dhcp.@dnsmasq[1].logfacility') [ "${TEST_UCI_SECOND:-0}" = 1 ] || exit 1; echo /dev/null ;;
*) exit 1 ;;
esac
EOF
chmod +x "$test_dir/bin/uci"
export PATH="$test_dir/bin:$PATH"
export TEST_UCI_FACILITY="" TEST_UCI_SECOND=0

run_case() {
	local name="$1" static="$2" facility="$3" second="${4:-0}"
	local raw="$test_dir/$name.raw" generated="$test_dir/$name.conf"
	printf '%s\n' "$static" > "$raw"
	printf 'conf-file=%s\n' "$raw" > "$generated"
	if [[ -n "$facility" || "$second" == 1 ]]; then
		printf 'log-facility=%s\n' "${facility:-/dev/null}" >> "$generated"
	fi
	export TEST_UCI_FACILITY="$facility" TEST_UCI_SECOND="$second"
	if [[ "$name" == duplicate ]]; then
		if "$dnsmasq_bin" --test --conf-file="$generated" > "$test_dir/before.log" 2>&1; then
			echo 'Duplicate fixture unexpectedly passed before migration' >&2
			exit 1
		fi
		grep -q 'illegal repeated keyword' "$test_dir/before.log"
	fi
	sh "$helper" "$raw"
	"$dnsmasq_bin" --test --conf-file="$generated"
	cp "$raw" "$raw.once"
	sh "$helper" "$raw"
	cmp "$raw" "$raw.once"
	if [[ -z "$facility" && "$second" == 0 ]]; then
		cmp "$raw" <(printf '%s\n' "$static")
	fi
	echo "PASS: $name (parser and repeat application)"
}

run_case fresh '# no static logging override' ''
run_case raw-only 'log-facility=/tmp/custom-dnsmasq.log' ''
run_case uci-only '#log-facility=/dev/null' /dev/null
run_case duplicate 'log-facility=/dev/null' /dev/null
run_case custom-uci '  log-facility=/dev/null' /tmp/custom-dnsmasq.log
run_case second-instance 'log-facility=/dev/null' '' 1
