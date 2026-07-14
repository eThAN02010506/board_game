.PHONY: test seed api web

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

seed:
	PYTHONPATH=src python3 -m ai_kp.seeds.demo

api:
	uvicorn ai_kp.api.main:app --reload

web:
	cd apps/web && npm run dev

