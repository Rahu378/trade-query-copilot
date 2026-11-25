.PHONY: seed bench dev test app site

seed:
	python -m copilot.seed

bench:
	python -m copilot.benchmark

dev:
	python -m copilot.benchmark --set dev -v

test:
	python -m pytest -q

app:
	streamlit run app.py

site:
	python -m copilot.export_site
