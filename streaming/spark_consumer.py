"""Spark Structured Streaming consumer for the retail invoice stream.

Reads JSON messages from Kafka, applies event-time tumbling windows with a
watermark for late arrivals, and writes the aggregates into PostgreSQL through
foreachBatch. The window bounds form the primary key, so a replayed micro-batch
overwrites its previous result rather than double-counting it.

    spark-submit \
        --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1,org.postgresql:postgresql:42.7.3 \
        streaming/spark_consumer.py
"""
from __future__ import annotations

import os

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "retail.invoices")
CHECKPOINT = os.getenv("SPARK_CHECKPOINT", "/tmp/checkpoints/retail")
WINDOW = os.getenv("STREAM_WINDOW", "1 hour")
WATERMARK = os.getenv("STREAM_WATERMARK", "2 hours")

PG_URL = os.getenv("PG_URL", "jdbc:postgresql://postgres:5432/retail")
PG_USER = os.getenv("PG_USER", "retail")
PG_PASSWORD = os.getenv("PG_PASSWORD", "retail")

SCHEMA = StructType([
    StructField("invoice_no",      StringType(),    False),
    StructField("stock_code",      StringType(),    False),
    StructField("description",     StringType(),    True),
    StructField("quantity",        IntegerType(),   False),
    StructField("unit_price",      DoubleType(),    False),
    StructField("line_revenue",    DoubleType(),    False),
    StructField("customer_id",     StringType(),    True),
    StructField("country",         StringType(),    False),
    StructField("is_cancellation", BooleanType(),   False),
    StructField("event_time",      TimestampType(), False),
])

_JDBC = {"user": PG_USER, "password": PG_PASSWORD, "driver": "org.postgresql.Driver"}


def build_session() -> SparkSession:
    return (SparkSession.builder
            .appName("retail-live-kpi")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.shuffle.partitions", "4")
            .config("spark.sql.streaming.metricsEnabled", "true")
            .getOrCreate())


def read_stream(spark: SparkSession) -> DataFrame:
    raw = (spark.readStream
           .format("kafka")
           .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
           .option("subscribe", KAFKA_TOPIC)
           .option("startingOffsets", "earliest")
           .option("maxOffsetsPerTrigger", 20_000)
           .option("failOnDataLoss", "false")
           .load())

    return (raw
            .select(F.from_json(F.col("value").cast("string"), SCHEMA).alias("e"))
            .select("e.*")
            .filter(F.col("event_time").isNotNull()))


def aggregate_overall(events: DataFrame) -> DataFrame:
    return (events
            .withWatermark("event_time", WATERMARK)
            .groupBy(F.window("event_time", WINDOW))
            .agg(
                F.sum(F.when(~F.col("is_cancellation"), F.col("line_revenue"))
                       .otherwise(F.lit(0.0))).alias("gross_revenue"),
                F.sum(F.when(F.col("is_cancellation"), F.abs(F.col("line_revenue")))
                       .otherwise(F.lit(0.0))).alias("returns_value"),
                F.sum("line_revenue").alias("net_revenue"),
                F.approx_count_distinct("invoice_no").alias("order_count"),
                F.count(F.lit(1)).alias("line_count"),
                F.sum(F.when(~F.col("is_cancellation"), F.col("quantity"))
                       .otherwise(F.lit(0))).alias("units_sold"))
            .select(
                F.col("window.start").alias("window_start"),
                F.col("window.end").alias("window_end"),
                F.round("gross_revenue", 2).alias("gross_revenue"),
                F.round("returns_value", 2).alias("returns_value"),
                F.round("net_revenue", 2).alias("net_revenue"),
                F.col("order_count").cast("int").alias("order_count"),
                F.col("line_count").cast("int").alias("line_count"),
                F.col("units_sold").cast("long").alias("units_sold")))


def aggregate_country(events: DataFrame) -> DataFrame:
    return (events
            .withWatermark("event_time", WATERMARK)
            .groupBy(F.window("event_time", WINDOW), F.col("country"))
            .agg(
                F.sum(F.when(~F.col("is_cancellation"), F.col("line_revenue"))
                       .otherwise(F.lit(0.0))).alias("gross_revenue"),
                F.count(F.lit(1)).alias("line_count"))
            .select(
                F.col("window.start").alias("window_start"),
                F.col("window.end").alias("window_end"),
                F.col("country"),
                F.round("gross_revenue", 2).alias("gross_revenue"),
                F.col("line_count").cast("int").alias("line_count")))


def _upsert(batch: DataFrame, table: str, staging: str, keys: list[str]) -> None:
    """Write the micro-batch to a staging table then merge it into the target.

    Structured Streaming can redeliver a batch after a failure, so an append
    would double-count. Writing to staging and merging on the window key makes
    the write idempotent, which is what makes the output safe to trust.
    """
    (batch.write
          .mode("overwrite")
          .option("truncate", "true")
          .jdbc(PG_URL, staging, properties=_JDBC))

    cols = batch.columns
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    merge = (
        f"INSERT INTO {table} ({', '.join(cols)}, updated_at) "
        f"SELECT {', '.join(cols)}, now() FROM {staging} "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}, updated_at = now()"
    )

    session = batch.sparkSession
    jvm = session._jvm
    conn = jvm.java.sql.DriverManager.getConnection(PG_URL, PG_USER, PG_PASSWORD)
    try:
        stmt = conn.createStatement()
        stmt.execute(merge)
        stmt.close()
    finally:
        conn.close()


def sink_overall(batch: DataFrame, batch_id: int) -> None:
    if batch.isEmpty():
        return
    batch.persist()
    try:
        _upsert(batch, "stream.live_kpi", "stream._stg_live_kpi",
                ["window_start", "window_end"])
        rows = batch.count()
        latest = batch.agg(F.max("window_end")).collect()[0][0]
        print(f"  batch {batch_id:>5}  windows={rows:<4} latest_event={latest}")
    finally:
        batch.unpersist()


def sink_country(batch: DataFrame, batch_id: int) -> None:
    if batch.isEmpty():
        return
    _upsert(batch, "stream.live_country", "stream._stg_live_country",
            ["window_start", "window_end", "country"])


def main() -> None:
    spark = build_session()
    spark.sparkContext.setLogLevel("WARN")
    print(f"  kafka   {KAFKA_BOOTSTRAP} / {KAFKA_TOPIC}")
    print(f"  window  {WINDOW}   watermark {WATERMARK}")
    print(f"  sink    {PG_URL}")

    events = read_stream(spark)

    overall_query = (aggregate_overall(events).writeStream
          .outputMode("update")
          .foreachBatch(sink_overall)
          .option("checkpointLocation", f"{CHECKPOINT}/overall")
          .trigger(processingTime="10 seconds")
          .start())

    country_query = (aggregate_country(events).writeStream
          .outputMode("update")
          .foreachBatch(sink_country)
          .option("checkpointLocation", f"{CHECKPOINT}/country")
          .trigger(processingTime="10 seconds")
          .start())

    print(f"  queries running: {overall_query.name or 'overall'}, "
          f"{country_query.name or 'country'}")
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
