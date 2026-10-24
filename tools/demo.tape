# VHS recipe for the README demo (https://github.com/charmbracelet/vhs).
# From the repo root, with pyxtrace and pytest on PATH:
#
#   vhs tools/demo.tape
#
# tools/make_demo.py builds the throwaway repo; everything after that is typed
# into a real shell and shows pyxtrace's real output.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 17
Set Width 1240
Set Height 680
Set Padding 24
Set TypingSpeed 45ms
Set Theme "Catppuccin Mocha"

Hide
Type `export PS1='$ ' GIT_CONFIG_GLOBAL=/dev/null GIT_PAGER=cat` Enter
Type `cd "$(python tools/make_demo.py)" && clear` Enter
Sleep 1s
Show

# 1. an ordinary test, nothing written for pyxtrace
Type "cat tests/test_orders.py" Sleep 400ms Enter
Sleep 4s

# 2. the change on this branch: the batched lookup is gone
Type "git diff main" Sleep 400ms Enter
Sleep 7s

Hide
Type "clear" Enter
Show

# 3. the gate: runs the tests on both commits and compares the counts
Type "pyxtrace diff main HEAD" Sleep 400ms Enter
Wait+Screen@60s /changed function/
Sleep 8s
Type "echo $?" Sleep 400ms Enter
Sleep 6s
