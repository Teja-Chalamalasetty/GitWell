# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability**. Include what you found, how to reproduce it, and the version or commit
you tested. You will get a reply as soon as the maintainer can read it, and a fix or a clear
explanation after that.

## Scope

GitWell reads GitHub repositories and stores scan results. Things we treat as security issues:

- a way to read, log or return a secret value or a GitHub token,
- a way to post a GitHub issue without an administrator's approval, or to post one twice,
- authentication or role bypass, or access to another user's data,
- the scanner being made to fetch from a host other than GitHub, or to exhaust memory or disk.

## Handling secrets

GitWell reads its tokens from the environment only and never logs or returns them. Keep your `.env`
file out of version control (it is git-ignored). If a token is ever exposed, revoke it on GitHub and
create a new one.
