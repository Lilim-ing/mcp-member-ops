.PHONY: demo test compliance

PY ?= python

demo:
	$(PY) -m mcpops.cli list

call:
	$(PY) -m mcpops.cli call segment_summary --args "{\"dimension\":\"member_level\",\"month\":\"2026-09\"}"

test:
	$(PY) -m pytest

compliance:
	$(PY) scripts/compliance_check.py .
