# Security Policy

## Reporting a Vulnerability

We take the security of Guidewire seriously. If you believe you have found a
security vulnerability, please report it responsibly.

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via one of the following methods:

- **GitHub Security Advisories**: Use the
  [Report a Vulnerability](https://github.com/HarmenBakhuis/Guidewire/security/advisories/new)
  feature to privately disclose the issue.
- **Email**: Send a description of the vulnerability to the maintainers.

### What to Include

Please include the following information in your report:

- Description of the vulnerability
- Steps to reproduce the issue
- Affected versions
- Potential impact
- Any suggested mitigations

### Response Timeline

- We will acknowledge receipt of your report within **48 hours**
- We will provide an initial assessment within **5 business days**
- We will keep you informed of progress toward a fix

### Disclosure Policy

- We ask that you give us a reasonable amount of time to address the
  vulnerability before public disclosure
- We will credit researchers who report vulnerabilities responsibly

## Security Best Practices

When using Guidewire:

- Only grant accessibility permissions to trusted applications
- Review automation scripts before executing them
- Be cautious when automating applications that handle sensitive data
  (passwords, financial information, personal data)
- Use the privacy controls (`guidewire.privacy`) to redact sensitive fields in
  snapshots and logs

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x     | :white_check_mark: |

As the project is in early development (Alpha), only the latest version is
actively supported.
