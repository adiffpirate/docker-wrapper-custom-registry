.PHONY: test test-unit test-functional

test-unit:
	DOCKER_WRAPPER_DEBUG=true python3 -m unittest discover -s tests/unit -v

test-functional:
	python3 -m unittest discover -s tests/functional -v

test: test-unit test-functional
