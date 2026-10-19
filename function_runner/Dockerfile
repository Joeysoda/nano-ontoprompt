FROM python:3.12-slim
# Libraries user Functions may import. Pinned so a Function's behaviour does not
# change underneath a published release.
RUN pip install --no-cache-dir numpy==2.1.3 scipy==1.14.1
WORKDIR /runner
COPY server.py worker.py /runner/
# Unprivileged; compose runs it with a read-only filesystem and no writable mount.
USER 65534:65534
EXPOSE 8100
CMD ["python", "-I", "/runner/server.py"]
