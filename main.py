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
        primary_lang = accept_language.split(",")[0].strip()
        return primary_lang.lower()

    def fetch_models(self, lang):
        all_models = {}
        for model in ["iphone-16-pro", "iphone-16"]:
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

            self.color_info[model] = product_data["productSelectionData"][
                "displayValues"
            ]["dimensionColor"]
            all_models[model] = product_data["productSelectionData"]["products"]

        return all_models

    def parse_models(self, all_data):
        # 容量排序函數
        def capacity_key(cap):
            # 將容量轉為數字進行排序 (例：256gb -> 256, 1tb -> 1024)
            if "tb" in cap.lower():
                return int(cap.lower().replace("tb", "").strip()) * 1024
            else:
                return int(cap.lower().replace("gb", "").strip())

        models = {}
        for model_type, data in all_data.items():
            for product in data:
                model_name = product["familyType"]
                if model_name not in models:
                    models[model_name] = {
                        "colors": [],
                        "capacities": set(),
                        "part_numbers": [],
                    }

                color_code = product["dimensionColor"]
                capacity = product["dimensionCapacity"]
                part_number = product["partNumber"]

                # 確保顏色不重複加入
                if color_code not in [
                    color["code"] for color in models[model_name]["colors"]
                ]:
                    color_data = self.color_info[model_type].get(color_code, {})
                    models[model_name]["colors"].append(
                        {
                            "code": color_code,
                            "name": color_data.get("value", color_code),
                        }
                    )

                models[model_name]["capacities"].add(capacity)

                # 確保 part_number 不重複
                part_info = {
                    "color": color_code,
                    "capacity": capacity,
                    "part_number": part_number,
                }
                if part_info not in models[model_name]["part_numbers"]:
                    models[model_name]["part_numbers"].append(part_info)

        # 排序邏輯
        for model_name, model in models.items():
            # 1. 將 set 轉換為 list，這樣可以被 JSON 序列化
            model["capacities"] = sorted(list(model["capacities"]), key=capacity_key)

            # 2. 顏色排序順序表，根據自訂的順序
            color_order = {
                color: index
                for index, color in enumerate(
                    self.color_info[model_type]["variantOrder"]
                )
            }

            # 3. 根據顏色和容量進行兩級排序
            model["part_numbers"] = sorted(
                model["part_numbers"],
                key=lambda p: (
                    color_order.get(p["color"], 999),
                    capacity_key(p["capacity"]),
                ),
            )
        return models

    def get_models(self, lang):
        data = self.fetch_models(lang)
        return self.parse_models(data)

    def fetch_and_parse_apple_regions(self):
        response = requests.get(self.regions_url, headers=self.headers)

        if response.status_code != 200:
            return {
                "error": f"Failed to fetch the webpage. Status code: {response.status_code}"
            }

        soup = BeautifulSoup(response.content, "html.parser")
        sections = soup.find_all("section", class_="category")

        result = {}
        for section in sections:
            region_name = section.get("data-analytics-section-engagement", "").split(
                ":"
            )[-1]
            countries = []

            for li in section.find_all("li"):
                a_tag = li.find("a")
                if a_tag:
                    name_span = a_tag.find("span", property="schema:name")
                    lang_meta = a_tag.find("meta", property="schema:inLanguage")

                    # 使用正則表達式清理 URL
                    url = re.sub(r"^/|/$", "", a_tag.get("href", ""))

                    country = {
                        "name": name_span.text.strip() if name_span else "Unknown",
                        "lang_tag": url,
                        "analytics_title": a_tag.get("data-analytics-title"),
                        "language": lang_meta["content"] if lang_meta else None,
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
        "data": data,
    }
    return jsonify(response), http_status


@app.route("/models", methods=["GET"])
def get_models():
    try:
        accept_language = request.headers.get("Accept-Language", "")
        lang = api.get_language(accept_language)
        models = api.get_models(lang)
        return format_response(
            200, "success", "Successfully retrieved iPhone models", models
        )
    except Exception as e:
        return format_response(
            500, "error", f"An error occurred while retrieving iPhone models: {str(e)}"
        )


@app.route("/regions", methods=["GET"])
def get_apple_regions():
    try:
        regions = api.fetch_and_parse_apple_regions()
        if "error" in regions:
            return format_response(400, "error", regions["error"])
        return format_response(
            200, "success", "Successfully retrieved Apple regions", regions
        )
    except Exception as e:
        return format_response(
            500, "error", f"An error occurred while retrieving Apple regions: {str(e)}"
        )


@app.route("/")
def home():
    return "API is running!"


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
