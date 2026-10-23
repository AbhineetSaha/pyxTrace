# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 3.x | Yes |
| below 3.0 | No; please upgrade |

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately,
either through GitHub's **Report a vulnerability** button on the
[Security tab](https://github.com/AbhineetSaha/pyxTrace/security), or by email to
abhineetsaha.2004@gmail.com.

Include what you found, how to reproduce it, and which version you used. You'll
get an acknowledgement within a week, and we'll keep you updated until it's fixed.

## What counts

pyxtrace runs the script you point it at, with your permissions, exactly like
`python script.py` does. Running code is its job, so that is not a vulnerability.

Things that would be:

- a crafted `.pyxt` run file that makes `pyxtrace diff` execute code, write files,
  or report a pass for a run that regressed
- anything that makes pyxtrace send data over the network (it never should)
- a file name or function name that injects terminal escape sequences into the output
