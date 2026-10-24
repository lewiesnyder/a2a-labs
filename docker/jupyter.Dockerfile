FROM a2a-labs:dev
CMD ["sh", "-c", "jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --IdentityProvider.token=${JUPYTER_TOKEN:-a2a-labs} --ServerApp.root_dir=/app --allow-root"]
