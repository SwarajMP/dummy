import google.generativeai as genai

genai.configure(api_key="AIzaSyBAC7q1YfZwXuR3VLYx_fvXOKqkkzwmk78")

models = genai.list_models()  # returns a list of available models
for m in models:
    print(m.name)
