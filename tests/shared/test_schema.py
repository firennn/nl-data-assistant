import pytest

from shared.schema import describe_schema, schema_to_prompt


def test_describe_schema_lists_tables_without_internal(sample_db):
    schema = describe_schema(sample_db)
    assert "orders" in schema.table_names
    assert "_schema_docs" not in schema.table_names


def test_describe_schema_columns_keys_and_docs(sample_db):
    orders = describe_schema(sample_db).table("orders")
    assert orders.row_count == 3
    assert orders.description == "One row per order."
    assert orders.column("order_id").is_pk
    assert orders.column("customer_id").fk_ref == "customers.customer_id"
    assert orders.column("status").description.startswith("Order status")
    assert set(orders.column("status").sample_values) <= {"delivered", "canceled"}


def test_describe_schema_filter_and_no_samples(sample_db):
    schema = describe_schema(sample_db, tables=["products"], sample_values=0)
    assert schema.table_names == ["products"]
    assert all(c.sample_values == [] for c in schema.tables[0].columns)


def test_describe_schema_unknown_table(sample_db):
    with pytest.raises(KeyError):
        describe_schema(sample_db, tables=["nope"])


def test_schema_to_prompt(sample_db):
    text = schema_to_prompt(describe_schema(sample_db))
    assert "TABLE orders (3 rows): One row per order." in text
    assert "customer_id TEXT FK -> customers.customer_id" in text
    assert "samples:" in text
    assert "samples:" not in schema_to_prompt(describe_schema(sample_db), include_samples=False)


def test_schema_to_prompt_skips_samples_for_id_columns(sample_db):
    lines = schema_to_prompt(describe_schema(sample_db)).splitlines()
    order_id_line = next(line for line in lines if line.strip().startswith("- order_id"))
    status_line = next(line for line in lines if line.strip().startswith("- status"))
    assert "samples:" not in order_id_line
    assert "samples:" in status_line
