.PHONY: seed bench dev test app site pages

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

# the Pages site is served from the gh-pages branch, a copy of docs/
pages:
	git subtree split --prefix docs -b gh-pages-tmp
	git push -f origin gh-pages-tmp:gh-pages
	git branch -D gh-pages-tmp
