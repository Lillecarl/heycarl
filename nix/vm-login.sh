# A browser's sign-in, step by step: Hey Carl, dex's password form, back.
# Prints the session cookie (name=value). Fails if any step does.
#
#   heycarl-login EMAIL [PASSWORD]
#
# HEYCARL_PUBLIC_URL names the site. The steps run separately because the
# last redirect goes to that URL, which only the unix socket reaches here.
email=$1
password=${2:-password}
public=${HEYCARL_PUBLIC_URL:-https://hey.example.com}
sock=/run/heycarl/kernel.sock
prefix=
case $public in https://*) prefix=__Host- ;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cookie_value() { grep -i "^set-cookie: $1=" "$2" | head -1 | cut -d' ' -f2 | cut -d';' -f1; }

curl -s --unix-socket "$sock" -D "$work/start" -o /dev/null 'http://hey/auth/login?next=/'
dex=$(grep -i '^location:' "$work/start" | cut -d' ' -f2 | tr -d '\r')
login_cookie=$(cookie_value "${prefix}heycarl-login" "$work/start")

# dex has one connector, so it goes straight to its password form.
form=$(curl -s -L -c "$work/jar" -b "$work/jar" -o /dev/null -w '%{url_effective}' "$dex")
callback=$(curl -s -c "$work/jar" -b "$work/jar" -o /dev/null -w '%{redirect_url}' \
  --data-urlencode "login=$email" --data-urlencode "password=$password" "$form")
# An approval page comes between only without skipApprovalScreen.
case $callback in
  */approval\?*)
    callback=$(curl -s -c "$work/jar" -b "$work/jar" -o /dev/null -w '%{redirect_url}' "$callback")
    ;;
esac
case $callback in
  "$public/auth/callback?"*) ;;
  *) echo "dex did not send us back: $callback" >&2; exit 1 ;;
esac

curl -s --unix-socket "$sock" -D "$work/back" -o /dev/null \
  -H "cookie: $login_cookie" "http://hey${callback#"$public"}"
grep -q '^HTTP/1.1 303' "$work/back" || { cat "$work/back" >&2; exit 1; }
cookie_value "${prefix}heycarl" "$work/back"
