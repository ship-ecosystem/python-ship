FROM alpine:3.24.2 AS config
ARG GARAGE_RPC_PUBLIC_ADDR=garage:3901
COPY garage.toml /garage.toml
RUN sed -i "s/__GARAGE_RPC_PUBLIC_ADDR__/${GARAGE_RPC_PUBLIC_ADDR}/" /garage.toml

FROM dxflrs/garage:v2.4.1
COPY --from=config /garage.toml /etc/garage.toml
