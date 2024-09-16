from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import js2py
import re
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

class iPhoneModelsAPI:
    def __init__(self):
        self.base_url = "https://www.apple.com"
        self.iphone_url = self.base_url + "/{lang}/shop/buy-iphone/{model}"
        self.regions_url = self.base_url + "/choose-country-region/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.models = None
        self.color_info = {}

    def get_language(self, accept_language):
        primary_lang = accept_language.split(',')[0].strip()
        return primary_lang.lower()

    def fetch_models(self, lang):
        all_models = {}
        for model in ['iphone-16-pro', 'iphone-16']:
            url = self.iphone_url.format(lang=lang, model=model)
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                raise Exception(f"無法獲取 {model} 型號數據。請檢查您的網絡連接。")

            script_content = response.text
            start_index = script_content.find("window.PRODUCT_SELECTION_BOOTSTRAP")
            if start_index == -1:
                raise Exception(f"無法在 {model} 頁面中找到產品數據。")

            end_index = script_content.find("</script>", start_index)
            if end_index == -1:
                raise Exception(f"無法解析 {model} 產品數據。")

            js_code = script_content[start_index:end_index]
            
            context = js2py.EvalJs()
            context.execute(js_code)
            product_data = context.PRODUCT_SELECTION_BOOTSTRAP.to_dict()
            
            self.color_info[model] = product_data['productSelectionData']['displayValues']['dimensionColor']
            all_models[model] = product_data['productSelectionData']['products']

        return all_models

    def parse_models(self, all_data):
        models = {}
        for model_type, data in all_data.items():
            for product in data:
                model_name = product['familyType']
                if model_name not in models:
                    models[model_name] = {
                        'colors': {},
                        'capacities': set(),
                        'part_numbers': []
                    }

                color_code = product['dimensionColor']
                capacity = product['dimensionCapacity']
                part_number = product['partNumber']

                if color_code not in models[model_name]['colors']:
                    color_data = self.color_info[model_type].get(color_code, {})
                    models[model_name]['colors'][color_code] = color_data.get('value', color_code)

                models[model_name]['capacities'].add(capacity)
                models[model_name]['part_numbers'].append({
                    'color': color_code,
                    'capacity': capacity,
                    'part_number': part_number
                })

        for model in models.values():
            model['capacities'] = sorted(list(model['capacities']))
            color_order = {color: index for index, color in enumerate(self.color_info[model_type]['variantOrder'])}
            model['colors'] = dict(sorted(model['colors'].items(), key=lambda x: color_order.get(x[0], 999)))

        return models

    def get_models(self, lang):
        data = self.fetch_models(lang)
        return self.parse_models(data)

    def fetch_and_parse_apple_regions(self):
        response = requests.get(self.regions_url, headers=self.headers)

        if response.status_code != 200:
            return {"error": f"Failed to fetch the webpage. Status code: {response.status_code}"}

        soup = BeautifulSoup(response.content, 'html.parser')
        sections = soup.find_all('section', class_='category')
        
        result = {}
        for section in sections:
            region_name = section.get('data-analytics-section-engagement', '').split(':')[-1]
            countries = []
            
            for li in section.find_all('li'):
                a_tag = li.find('a')
                if a_tag:
                    name_span = a_tag.find('span', property="schema:name")
                    lang_meta = a_tag.find('meta', property="schema:inLanguage")
                    
                    # 使用正則表達式清理 URL
                    url = re.sub(r'^/|/$', '', a_tag.get('href', ''))

                    country = {
                        'name': name_span.text.strip() if name_span else "Unknown",
                        'lang_tag': url,
                        'analytics_title': a_tag.get('data-analytics-title'),
                        'language': lang_meta['content'] if lang_meta else None
                    }
                    countries.append(country)
            
            if region_name:
                result[region_name] = countries
        
        return result

api = iPhoneModelsAPI()

def format_response(http_status, status, message, data=None):
    response = {
        "status": "success" if http_status < 400 else "error",
        "msg": message,
        "data": data
    }
    return jsonify(response), http_status

@app.route('/models', methods=['GET'])
def get_models():
    try:
        accept_language = request.headers.get('Accept-Language', '')
        lang = api.get_language(accept_language)
        models = api.get_models(lang)
        return format_response(200, "success", "Successfully retrieved iPhone models", models)
    except Exception as e:
        return format_response(500, "error", f"An error occurred while retrieving iPhone models: {str(e)}")

@app.route('/regions', methods=['GET'])
def get_apple_regions():
    try:
        regions = api.fetch_and_parse_apple_regions()
        if "error" in regions:
            return format_response(400, "error", regions["error"])
        return format_response(200, "success", "Successfully retrieved Apple regions", regions)
    except Exception as e:
        return format_response(500, "error", f"An error occurred while retrieving Apple regions: {str(e)}")

@app.route('/')
def home():
    return "API is running!"

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)