#!/bin/sh
#
# Signs in against the sync server, proves the token works, signs out again,
# and checks the token really stopped working.
#
# Uses curl rather than Python's urllib on purpose: a python.org install on
# macOS ships with an empty certificate store until "Install Certificates"
# has been run, so urllib cannot verify TLS there while curl - which uses the
# system store - can. Python is used only to build and read JSON, never to
# make the request. The client itself is unaffected: it uses requests, which
# brings its own certificate bundle.
#
# The password is read without echo and never appears in the command line or
# the shell history.
URL=https://www.familiefaulstich.de/tc/index.php
printf 'Kontoname [frank]: '; read TCUSER; [ -z "$TCUSER" ] && TCUSER=frank
printf 'Kontopasswort: '; stty -echo; read TCPW; stty echo; echo

DEV=$(python3 -c 'import secrets;print(secrets.token_hex(8))')
BODY=$(TCUSER="$TCUSER" TCPW="$TCPW" DEV="$DEV" python3 -c '
import json,os
print(json.dumps({"username":os.environ["TCUSER"],"password":os.environ["TCPW"],
                  "device_uid":os.environ["DEV"],"device_name":"test"}))')

RESP=$(printf '%s' "$BODY" | curl -s -m 20 -X POST \
        -H 'Content-Type: application/json' --data-binary @- "$URL?a=login")
TOK=$(printf '%s' "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("token",""))')

if [ -z "$TOK" ]; then
  echo "anmelden : FEHLGESCHLAGEN -> $RESP"
else
  echo "anmelden : ok"
  echo "ping     : $(curl -s -m 20 -H "X-TC-Token: $TOK" "$URL?a=ping")"
  echo "abmelden : $(curl -s -m 20 -H "X-TC-Token: $TOK" "$URL?a=logout")"
  echo "danach   : $(curl -s -m 20 -H "X-TC-Token: $TOK" "$URL?a=ping")"
fi
unset TCPW BODY TOK
