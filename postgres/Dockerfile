# PINNED 2026-09-26 (docs/V3_PLAN.md 0.5) to the base the running data was last
# reindexed under: PostgreSQL 16.4 on Debian 11, glibc 2.31. The floating
# `postgres:16` tag has since moved to 16.15 on Debian 13 (glibc 2.41); text
# indexes sort by glibc's collation rules, so rebuilding against a different
# glibc silently mis-orders them. That is how the data (created under 2.36) came
# to run on 2.31. Changing this base needs, in the same step: a pg_dump backup,
# REINDEX DATABASE, and ALTER DATABASE ... REFRESH COLLATION VERSION.
FROM postgres:16.4-bullseye@sha256:f47712331e3b7069c954893719347ff304839b2933bae84e30a0ba99974c8c32

# Debian and PostgreSQL both moved bullseye to archive servers in 2026: Debian's live mirrors list security updates
# they no longer serve, and apt.postgresql.org answers 404. Debian's archive serves plain HTTP. PostgreSQL's archive
# serves HTTPS only, and the 2021 base image's CA bundle does not trust its certificate, so TLS peer checks are
# skipped for that one host only. apt still verifies every package against each repository's signing key, so a
# tampered package is rejected either way.
RUN sed -i 's#http://deb.debian.org#http://archive.debian.org#g' /etc/apt/sources.list && \
    sed -i 's#https\?://apt.postgresql.org#https://apt-archive.postgresql.org#g' /etc/apt/sources.list.d/pgdg.list && \
    echo 'Acquire::https::apt-archive.postgresql.org::Verify-Peer "false";' > /etc/apt/apt.conf.d/99pgdg-archive && \
    apt-get update && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
        postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
