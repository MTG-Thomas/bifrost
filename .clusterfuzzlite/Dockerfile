FROM gcr.io/oss-fuzz-base/base-builder-python

COPY . $SRC/bifrost
WORKDIR $SRC/bifrost

COPY .clusterfuzzlite/build.sh $SRC/build.sh
RUN chmod +x $SRC/build.sh
