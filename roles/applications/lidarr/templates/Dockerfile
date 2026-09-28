# {{ ansible_managed }}
# Build context: the Lidarr source checkout ({{ lidarr_source_directory }}).

# Toolchain as in upstream CI (azure-pipelines.yml): Node 20 + yarn 1 for the
# UI, the exact .NET SDK pinned in global.json for the backend.
FROM node:20-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends libicu72 \
 && rm -rf /var/lib/apt/lists/*
# NuGetAudit: advisories published after a release otherwise fail its build
# (warnings are errors).
ENV DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    NuGetAudit=false
WORKDIR /src
COPY global.json ./
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh \
  | bash -s -- --jsonfile global.json --install-dir "$DOTNET_ROOT" \
 && ln -s "$DOTNET_ROOT/dotnet" /usr/local/bin/dotnet
COPY . .
# linuxserver/lidarr is Alpine, hence the musl runtime.
RUN ./build.sh --backend --frontend --packages -r linux-musl-x64 -f net8.0 \
 && rm -rf _artifacts/linux-musl-x64/net8.0/Lidarr/Lidarr.Update

# Lidarr publishes no Dockerfile; linuxserver's (docker-lidarr) installs the
# release tarball into /app/lidarr/bin. Keep their image (s6 init, PUID/PGID,
# packages, package_info) and swap only those binaries, minus the updater as
# they do.
FROM {{ lidarr_image_name }}:{{ lidarr_image_version }}
RUN rm -rf /app/lidarr/bin
COPY --from=build /src/_artifacts/linux-musl-x64/net8.0/Lidarr /app/lidarr/bin
