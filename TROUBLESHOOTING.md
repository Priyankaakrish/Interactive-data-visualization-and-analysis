# Troubleshooting the real-time stack

Every entry below is a problem actually hit while bringing this up, with the
cause and the fix. Ordered by how often it bites.

## Kafka never reports healthy; the stack aborts

    dependency failed to start: container retail-kafka is unhealthy

**Cause.** A half-initialised KRaft metadata volume. Kafka writes cluster
metadata on first start; if the container is killed partway through, the broker
starts but never finishes becoming a controller. A plain `docker compose down`
leaves that volume intact, so every retry inherits the same broken state.

**Fix.** Remove the volumes, not just the containers:

    docker compose -f deployment\docker-compose.yml down -v --remove-orphans
    docker compose -f deployment\docker-compose.yml up -d

Also give the healthcheck room - each probe spawns a JVM:
`start_period: 60s`, `timeout: 12s`, `retries: 20`.

## A Spark checkpoint overrides startingOffsets

**Cause.** `startingOffsets` only applies when no checkpoint exists. Changing
it on a job that already has one does nothing - the job resumes from the
checkpoint. A consumer that repeatedly restarted with `latest` ends up parked
at the tail and silently stops advancing.

**Symptom.** `live/summary` returns a figure that never changes;
`MAX(updated_at)` in `stream.live_kpi` is hours old.

**Fix.** Delete the checkpoint. Note that `stop` does not release the volume -
the container still exists and holds it:

    docker compose -f deployment\docker-compose.yml rm -sf spark
    docker volume rm retail-bi_sparkcheck
    docker compose -f deployment\docker-compose.yml up -d spark

Verify it is writing:

    docker compose -f deployment\docker-compose.yml exec -T postgres psql -U retail -d retail -c "SELECT COUNT(*) AS windows, SUM(line_count) AS lines, now() - MAX(updated_at) AS age FROM stream.live_kpi;"

An `age` under a minute means the consumer is alive.

## Bind for 0.0.0.0:29092 failed: port is already allocated

**Cause.** Another Compose project publishes the same host port. Two projects
can share internal service names - each gets its own bridge network - but host
port bindings are global.

**Fix.** This stack uses 55432, 29192 and 8000 to stay clear of the common
ones. To find the holder:

    Get-NetTCPConnection -LocalPort 29192 -State Listen | ForEach-Object { Get-Process -Id $_.OwningProcess }

If you change Kafka's host port, change `KAFKA_ADVERTISED_LISTENERS` to match,
or clients get handed an address they cannot reach.

## services.kafka.ports must be a array

**Cause.** A `ports:` key with every entry deleted. YAML needs the key removed
too, not just its contents.

**Fix.** Restore from backup and edit rather than delete. Always validate:

    docker compose -f deployment\docker-compose.yml config --quiet

Silence means valid.

## Build uploads hundreds of MB, or fails on locked files

**Cause.** `.dockerignore` in the wrong place. Docker reads it from the build
context root, not from beside the Dockerfile. With `context: ..` that is the
project root.

**Fix.** `Copy-Item deployment\.dockerignore .dockerignore -Force`
A correct build shows a context of a few hundred KB, not hundreds of MB.

## kafka-python is not installed, in the producer

**Cause.** `kafka-python` 2.0.2 predates Python 3.12 and fails on import. The
producer catches the ImportError and reports it as missing.

**Fix.** Use the maintained fork - drop-in, same import name:
`kafka-python-ng==2.2.3`

## Spark dies with UnknownTopicOrPartitionException

**Cause.** The consumer started before the topic existed.

**Fix.**

    docker compose -f deployment\docker-compose.yml exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic retail.invoices --partitions 3 --replication-factor 1
    docker compose -f deployment\docker-compose.yml restart spark

## Containers vanish; ExitCode=137

**Cause.** Out of memory. Spark, Kafka and PostgreSQL together need more than
the WSL 2 default.

**Fix.** On Docker Desktop with the WSL 2 backend there is no memory slider -
it lives in `.wslconfig` in your user profile:

    [wsl2]
    memory=8GB
    processors=4
    swap=4GB

Then `wsl --shutdown`, and restart Docker Desktop. Use about half your RAM.

## request returned 500 Internal Server Error ... /_ping

**Cause.** The Docker Desktop engine has fallen over, usually after heavy
rebuilds. Nothing to do with the compose file.

**Fix.** Kill and restart Docker Desktop, and wait for `docker info` to answer.

## live/summary returns streaming: false

**Cause.** The API connects to the stream database once, in the lifespan
handler. If PostgreSQL was unreachable at that moment, or restarted later, the
engine stays unset for the life of the process.

**Fix.** `docker compose -f deployment\docker-compose.yml restart api`

**Known limitation.** A lazy reconnect - retry `init()` on the first `/live`
request when the engine is unset - would let the service self-heal.

## Swagger shows Failed to fetch

The page was cached; the API container has since stopped. Restart it and
hard-reload with Ctrl+F5.

## retail-kafka-init shows as stopped

Not a problem. It creates the topic and exits - `restart: "no"` is deliberate.
A grey dot is correct. Verify the topic instead of the container:

    docker compose -f deployment\docker-compose.yml exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic retail.invoices

## The live figure stops rising

Expected once the whole source file has been through. The producer loops the
same export, and the merge is idempotent on `(window_start, window_end)`, so
replays overwrite rather than accumulate. A total that settles at the true
figure is the system working.

Small dips in `line_count` are also normal - windows inside the two-hour
watermark get recomputed as late messages arrive.

## lag_seconds is an enormous number

Not a problem. It is the gap between the newest event timestamp and now. The
source data is from 2011, so the lag is about fifteen years in seconds. The
arithmetic is right; the metric only means something against a live feed.
