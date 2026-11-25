-- Upload (and re-upload after every edit) the semantic model Cortex Analyst reads.
--   snow sql -f snowflake/04_semantic_model.sql
PUT file://semantic_model/trade_ops.yaml @TRADE_OPS.CORE.SEMANTIC_MODELS AUTO_COMPRESS = FALSE OVERWRITE = TRUE;
LIST @TRADE_OPS.CORE.SEMANTIC_MODELS;
