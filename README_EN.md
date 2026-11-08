# mymediafolo

[简体中文](README.md) | English

![License](https://img.shields.io/badge/license-GPL--3.0-blue?style=for-the-badge)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Docker-blue?style=for-the-badge)

Based on MoviePilot v2.15.6 (upstream commit `684e76e`), with additional media-management compatibility and local-mounted-drive post-processing capabilities.

# For learning and personal communication only. Please do not promote this project on platforms in mainland China.

Release, image, and external service addresses are injected by the deployer.

## Key Features

- Focuses on the core media automation flow: subscriptions, search, downloads, file organization, scraping, media server refresh, and notifications.
- Uses a separated backend/frontend architecture: FastAPI for the backend and Vue 3 for the frontend.
- Connects download clients, media servers, metadata providers, message channels, plugins, workflows, and AI Agent capabilities.
- See `docs/` for the management API, local-mounted Google Drive processing, and deployment notes.

## Installation and Usage

Docker is the recommended deployment model. Image names, frontend assets, plugin assets, and update endpoints are supplied by the release owner; this repository does not ship upstream service defaults.

- PostgreSQL setup: [docs/postgresql-setup.md](docs/postgresql-setup.md)

mymediafolo can also be installed and managed from a local source checkout:

```shell
bash scripts/bootstrap-local.sh --repo-url "$MYMEDIAFOLO_REPOSITORY_URL"
```

After installation, use the `moviepilot` command for initialization, service management, updates, and configuration. See [docs/cli.md](docs/cli.md) for the full command reference.

## Agent

1. mymediafolo includes a built-in AI Agent. After model configuration, it can call system tools through natural language to help with search, subscriptions, downloads, organization, diagnostics, and other management tasks.
2. Other agents can import the local `skills/` directory without relying on a preconfigured external repository:

   ```shell
   npx skills add ./skills
   ```

   Built-in skills live in [skills/](skills/). For custom skill authoring, see [skills/create-moviepilot-skill/SKILL.md](skills/create-moviepilot-skill/SKILL.md).
3. Other MCP clients can call mymediafolo tools through `/api/v1/mcp`. Authentication, client configuration, and tool APIs are documented in [docs/mcp-api.md](docs/mcp-api.md).

## Development

Before contributing, read the repository rules and local environment guide, keep changes focused, and validate them before opening a PR. Useful entry points:

- Rule index: [docs/rules/README.md](docs/rules/README.md)
- Development setup and local source run: [docs/development-setup.md](docs/development-setup.md)
- Testing guide: [docs/testing.md](docs/testing.md)
- New site adapter capture and Feature Request submission: [docs/site-adapter-capture.md](docs/site-adapter-capture.md)
- REST API documentation: see [docs/mcp-api.md](docs/mcp-api.md)
- Plugin development guide: maintain a local skill or plugin repository and inject its address explicitly.

## Attribution and License

- This project is based on MoviePilot v2.15.6 (upstream commit `684e76e`) and retains required copyright and attribution notices for the original project and third-party components.
- This repository is distributed under GPL-3.0. Redistributions must preserve the source, license, and attribution information.

## Disclaimer

- This software is for learning and personal communication only. It must not be used for commercial purposes or illegal activities. The software does not know how users choose to use it, and all responsibility rests with the user.
- The source code is open source and derived from other open-source code. If someone removes the relevant restrictions and redistributes or publishes modified versions that lead to liability events, the publisher of those modifications bears full responsibility. Public releases that bypass or alter the user authentication mechanism are not recommended.
- This project does not accept donations and has not published any donation page anywhere. The software itself is free of charge and does not provide paid services. Please verify information carefully to avoid being misled.

## Contributors

Contributors and release information are maintained by the deployment repository.
