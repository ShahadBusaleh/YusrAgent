# Single-container Hugging Face Docker Space: FastAPI on 127.0.0.1:8000
# (internal only), Streamlit on 7860 (the public port).
FROM python:3.10-slim

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/user/.cache/huggingface \
    API_BASE_URL=http://127.0.0.1:8000
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
# CPU-only torch keeps the image small (Spaces free tier has no GPU).
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --user -r requirements.txt

# Bake the embedding model into the image so cold starts don't download it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY --chown=user . .
RUN chmod +x start.sh

EXPOSE 7860
CMD ["./start.sh"]
