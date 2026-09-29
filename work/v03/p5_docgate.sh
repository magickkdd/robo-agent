#!/usr/bin/env bash
# The doc gate. v0.2's report names `verify_docs_v3.py` as "51 检查"; its true home is
# `/tmp/mw117/verify_docs_v3.py` (the v0.2 report §F row 93 records that a write to
# `/home/czx/mw117/` was a typo of mine, and that the real address is `/tmp/mw117/`). It is a
# /tmp instrument, not a committed one, so whether it still runs is a fact to measure rather
# than a claim to make.
#
# It is pointed at the v0.2 pair by construction (`DOCS` + a filename list). Running it tells
# us two things: whether v0.2's gate is still green, and whether it can be pointed at the v0.3
# pair without editing — which is what SPEC §9 P5' asks for ("门按新文件演进，演进要落账").
set -u
G=/tmp/mw117/verify_docs_v3.py
echo "gate: $G"
ls -la "$G" 2>&1 | head -1
echo "sha256: $(sha256sum "$G" | cut -c1-16)"
echo
echo "### what it is pointed at"
grep -n "^DOCS\|^PAIRS\|continuous-decision" "$G" | head -12
echo
echo "### run it as shipped (v0.2's own pair)"
cd /tmp/mw117 && /home/czx/miniforge3/envs/embodied/bin/python "$G" 2>&1 | tail -25
echo "GATE_RC=${PIPESTATUS[0]}"
