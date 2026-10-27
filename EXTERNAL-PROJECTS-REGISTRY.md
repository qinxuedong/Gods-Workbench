# External Projects Registry

## QIN-418 local image/text embeddings

The embedding runtime is an opt-in local install. It is not part of the base
application requirements or CI environment.

Install the pinned package set from the repository root:

~~~powershell
python -m pip install --require-hashes -r requirements-embedding.lock
~~~

The lock was generated from requirements-embedding.txt with
pip-compile --generate-hashes --constraint requirements.lock; the existing
global requirement files remain unchanged.

Download model weights explicitly to the configured GW_DATA_DIR data root.
For example, from the repository root:

~~~powershell
python -c "from gw.asset_registry.embedder import download_model_weights; download_model_weights()"
~~~

The default is OFA-Sys/chinese-clip-vit-base-patch16. To download the
comparison model, call download_model_weights("google/siglip-base-patch16-224").
Weight files stay in the runtime data root and are never stored in Git. Normal
embedding and search only load cached local files; they do not contact Hugging
Face. Without the optional packages or local weights, indexing and search use
the existing FTS5 path.

| Model | Source | Vector dimensions | Notes |
| --- | --- | ---: | --- |
| Chinese-CLIP ViT-B/16 | [Hugging Face model](https://huggingface.co/OFA-Sys/chinese-clip-vit-base-patch16) · [OFA-Sys project](https://github.com/OFA-Sys/Chinese-CLIP) | 512 | Default; best result in the MVP1-T4a Chinese retrieval comparison. The upstream project repository is MIT licensed; check the model artifact terms before redistribution. |
| SigLIP base patch16/224 | [Google model on Hugging Face](https://huggingface.co/google/siglip-base-patch16-224) | 768 | Comparison option; the model card lists Apache-2.0. |
