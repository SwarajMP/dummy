import google.generativeai as genai

genai.configure(api_key="api_key")

models = genai.list_models()  # returns a list of available models
for m in models:
    print(m.name)
