FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:8d46cb51bc42b45eeaa80b132a235f60a79154c788fe17012847a35304d8b804

COPY . $SRC/bifrost
WORKDIR $SRC/bifrost

COPY .clusterfuzzlite/build.sh $SRC/build.sh
RUN chmod +x $SRC/build.sh
