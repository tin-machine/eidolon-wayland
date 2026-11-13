# Security Policy

## Supported versions

Eidolon Wayland is under active development. There is **no stable, security-supported release** or published support window at present. The current `main` branch is a development snapshot, **not** a promise of a security-fix SLA. If the project later publishes supported releases, this section will be updated.

## Reporting a vulnerability

**Do not disclose an exploitable vulnerability, working exploit, credentials, private keys, host addresses, or sensitive logs in a public Issue, Discussion, or Pull Request.**

Once the repository is public and **GitHub Private vulnerability reporting** is enabled:

1. Open [Eidolon's Security page](https://github.com/tin-machine/eidolon-wayland/security).
2. Choose **Report a vulnerability** and submit the report **privately**.
3. Include the affected commit SHA/version, component and execution environment (Quest/Horizon OS, Linux host, Termux, etc.), attack prerequisites, impact, and a minimal reproduction. Redact secrets, personal data, and machine-identifying information.

If the private reporting feature is unavailable, create a **public Issue asking for a private security contact channel only**. Do **not** include vulnerability details or a proof of concept in that Issue. Do not assume this file or the Issues feature itself creates a private channel.

Reports concerning SSH authentication or host-key verification, remote execution and permissions, Wayland/Waypipe inputs, Android/JNI/Spatial SDK boundaries, window/session ownership, or build/release supply-chain integrity are especially useful. Report third-party issues here when they materially affect Eidolon, and consider notifying the upstream maintainer through their security process as well.

## Handling and disclosure

Maintainers will assess reports on a best-effort basis; no guaranteed acknowledgment, response, remediation, or disclosure timeline is offered. Please coordinate public disclosure with maintainers where practical, allowing time for investigation and remediation. Do not test against systems or accounts you do not own or have authorization to assess.

There is no bug-bounty or reward program.

## Public issues and contributions

Ordinary bugs and feature requests are welcome in [GitHub Issues](https://github.com/tin-machine/eidolon-wayland/issues), without secrets or sensitive logs. External pull-request submissions are restricted to repository collaborators; this does not limit private vulnerability reports.

日本語でも脆弱性を報告できます。公開Issueへ秘密情報や再現可能な攻撃手順を書かず、公開後に有効化するGitHubの **Private vulnerability reporting** を利用してください。未有効化の場合、詳細を伏せて非公開連絡手段だけをIssueで問い合わせてください。
