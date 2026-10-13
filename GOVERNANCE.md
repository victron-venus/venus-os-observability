# Governance and maintenance

venus-os-observability is developed in the public [GitHub repository](https://github.com/victron-venus/venus-os-observability). Contributors propose changes through issues and pull requests. Reviewers assess correctness, compatibility, security boundaries and test evidence. Maintainers decide scope, record decisions and merge after the required checks and reviews.

## Roles and decisions

[4alvit](https://github.com/4alvit) is the primary maintainer and repository administrator; administrative access was verified on 2026-10-13. The maintainer owns release acceptance, final scope decisions, access management and confidential security reports. Review automation assists review; it is not an independent human maintainer or a successor.

Discuss significant interface, dependency, platform or security changes in an issue before implementation. Describe the problem, alternatives, compatibility impact and migration plan. Small changes can use the pull request itself. Record the decision and its reasons publicly, except confidential vulnerability details handled under [SECURITY.md](SECURITY.md). Contributors may request reconsideration with new evidence; the maintainer should explain the final decision respectfully. Release responsibilities and acceptance are defined in [RELEASING.md](RELEASING.md).

Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Contributions must be compatible with [LICENSE](LICENSE). There is currently no mandatory DCO/CLA process and no claim of two independent knowledgeable maintainers.

## Continuity: open requirement

The owner confirmed on 2026-10-13 that no backup maintainer or verified emergency-access arrangement currently exists. OpenSSF Silver `access_continuity` remains **Unmet**. A public clone, bot approval or organization membership cannot recover repository administration, CI secrets, publication access and confidential reports by itself.

To close this gap, obtain a successor's consent or establish another real recovery mechanism, inventory the critical services, and test restoration within one week. Document the responsible role, recovery procedure and review date without publishing credentials. Until this is verified, do not present succession as an implemented policy.
