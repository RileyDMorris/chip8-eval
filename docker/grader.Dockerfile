# Grading container: runs a candidate emulator.py on hidden test inputs.
# Started with --network none; the driver runs as root only to launch the
# candidate as the unprivileged "candidate" user. Golden outputs never enter it.
FROM python:3.12-slim
RUN useradd --create-home --uid 1001 candidate \
 && mkdir -p /opt/grader /grade
COPY grader/driver.py /opt/grader/driver.py
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /grade
