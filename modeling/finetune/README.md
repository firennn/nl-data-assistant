# Text-to-SQL fine-tuning experiment (optional, owner2)

Goal: check whether a small open model fine-tuned on this schema can match the hosted LLM on
the evaluation set at lower cost.

## Outline
1. **Data**: build training pairs (question, SQL) for this schema. Sources: paraphrases of the
   evaluation questions' *templates* (never the evaluation questions themselves), plus a public
   text-to-SQL dataset for general SQL skills. Validate every SQL by running it with
   `shared.db.run_query`.
2. **Model**: a small open model (1-8B parameters) with LoRA fine-tuning.
3. **Serving**: expose it through a new provider class in `shared/llm.py`
   (`register_provider("local", ...)`), so the agent works unchanged with `LLM_PROVIDER=local`.
4. **Evaluation**: run `evaluation/` on the base model, the fine-tuned model and the hosted LLM,
   and compare execution accuracy, latency and cost.

## Rules
- Weights and checkpoints are never committed (see `.gitignore`); document how to reproduce them.
- Keep training scripts and configs in this folder.
