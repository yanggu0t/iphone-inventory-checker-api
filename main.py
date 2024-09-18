from quart import Quart, jsonify, request
from quart_cors import cors
import aiohttp
import asyncio
import js2py
import re
from bs4 import BeautifulSoup

app = Quart(__name__)
app = cors(app)


class iPhoneModelsAPI:
    def __init__(self):
        self.base_url = "https://www.apple.com"
        self.iphone_url = self.base_url + "/{lang}/shop/buy-iphone/{model}"
        self.regions_url = self.base_url + "/choose-country-region/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.models = ["iphone-16", "iphone-16-pro"]
        self.color_info = {}

    def get_language(self, accept_language):
        primary_lang = accept_language.split(",")[0].strip()
        return primary_lang.lower()

    async def fetch_model(self, session, lang, model):
        url = self.iphone_url.format(lang=lang, model=model)
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(f"無法獲取 {model} 型號數據。請檢查您的網絡連接。")
            return await response.text()

    async def fetch_models(self, lang):
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_model(session, lang, model) for model in self.models]
            responses = await asyncio.gather(*tasks)

        all_models = {}
        for model, response in zip(self.models, responses):
            if isinstance(response, Exception):
                print(f"獲取 {model} 時發生錯誤: {str(response)}")
                continue
            if response is None:
                continue
            script_content = response
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

            all_models[model] = product_data["productSelectionData"]["products"]
            self.color_info[model] = product_data["productSelectionData"][
                "displayValues"
            ]["dimensionColor"]

        return all_models

    def parse_models(self, all_data):
        def capacity_key(cap):
            if "tb" in cap.lower():
                return int(cap.lower().replace("tb", "").strip()) * 1024
            else:
                return int(cap.lower().replace("gb", "").strip())

        models = []
        for model_type, data in all_data.items():
            model_info = {}
            for product in data:
                model_id = product["familyType"]
                model_name = self.format_model_name(model_id)

                if model_name not in model_info:
                    model_info[model_name] = {
                        "id": model_id,
                        "name": model_name,
                        "colors": [],
                        "capacities": set(),
                        "part_numbers": [],
                    }

                color_code = product["dimensionColor"]
                capacity = product["dimensionCapacity"]
                part_number = product["partNumber"]

                if color_code not in [
                    color["code"] for color in model_info[model_name]["colors"]
                ]:
                    color_data = self.color_info[model_type].get(color_code, {})
                    model_info[model_name]["colors"].append(
                        {
                            "code": color_code,
                            "name": color_data.get("value", color_code),
                        }
                    )

                model_info[model_name]["capacities"].add(capacity)

                part_info = {
                    "color": color_code,
                    "capacity": capacity,
                    "part_number": part_number,
                }
                if part_info not in model_info[model_name]["part_numbers"]:
                    model_info[model_name]["part_numbers"].append(part_info)

            # 轉換為列表並排序
            for model in model_info.values():
                model["capacities"] = sorted(
                    list(model["capacities"]), key=capacity_key
                )

                color_order = {
                    color: index
                    for index, color in enumerate(
                        self.color_info[model_type]["variantOrder"]
                    )
                }

                model["part_numbers"] = sorted(
                    model["part_numbers"],
                    key=lambda p: (
                        color_order.get(p["color"], 999),
                        capacity_key(p["capacity"]),
                    ),
                )

                models.append(model)

        # 根據型號名稱排序
        models.sort(
            key=lambda x: (
                int(re.search(r"\d+", x["name"]).group()),
                "Pro Max" in x["name"],
                "Pro" in x["name"],
                "Plus" in x["name"],
            )
        )

        return models

    def format_model_name(self, model_id):
        parts = model_id.lower().split("iphone")
        if len(parts) > 1:
            number = parts[1].split("pro")[0].strip().replace("plus", "").strip()
            if "pro" in model_id.lower():
                if "max" in model_id.lower():
                    return f"iPhone {number} Pro Max"
                else:
                    return f"iPhone {number} Pro"
            elif "plus" in model_id.lower():
                return f"iPhone {number} Plus"
            else:
                return f"iPhone {number}"
        return model_id  # 如果無法解析，返回原始 ID

    async def get_models(self, lang):
        data = await self.fetch_models(lang)
        return self.parse_models(data)

    async def fetch_and_parse_apple_regions(self):

        exclude_titles = ['canada-french', 'hong-kong-english']  # 要排除的 analytics_title 列表

        async with aiohttp.ClientSession() as session:
            async with session.get(self.regions_url, headers=self.headers) as response:
                if response.status != 200:
                    return {
                        "error": f"無法獲取網頁。狀態碼：{response.status}"
                    }
                content = await response.text()

        soup = BeautifulSoup(content, "html.parser")
        sections = soup.find_all("section", class_="category")

        result = []
        for section in sections:
            region_name = section.get("data-analytics-section-engagement", "").split(":")[-1]

            # 跳過 europe 和 latin-america 區域
            if region_name.lower() in ["europe", "latin-america", "africa-mideast"]:
                continue
            
            countries = []

            for li in section.find_all("li"):
                a_tag = li.find("a")
                if a_tag:
                    name_span = a_tag.find("span", property="schema:name")
                    lang_meta = a_tag.find("meta", property="schema:inLanguage")
                    analytics_title = a_tag.get("data-analytics-title", "")

                    # 跳過 Unknown 名稱的國家
                    if not name_span or name_span.text.strip() == "Unknown":
                        continue
                    
                    # 跳過指定的 analytics_title
                    if analytics_title in exclude_titles:
                        continue

                    # 使用正則表達式清理 URL
                    url = re.sub(r"^/|/$", "", a_tag.get("href", ""))

                    # 處理中國的特殊情況
                    if "china" in analytics_title.lower():
                            url = "cn"

                    country = {
                        "name": name_span.text.strip(),
                        "lang_tag": url,
                        "analytics_title": analytics_title,
                        "language": lang_meta["content"] if lang_meta else None,
                    }
                    countries.append(country)

                    

            if region_name:
                result.append({
                    "title": region_name,
                    "regions": countries
                })

        return result

api = iPhoneModelsAPI()


async def format_response(http_status, status, message, data=None):
    response = {
        "status": status,
        "msg": message,
        "data": data,
    }
    return response, http_status


@app.route("/models", methods=["GET"])
async def get_models():
    try:
        accept_language = request.headers.get("Accept-Language", "")
        lang = api.get_language(accept_language)
        models = await api.get_models(lang)
        response, status_code = await format_response(
            200, "success", "Successfully retrieved iPhone models", models
        )
        return jsonify(response), status_code
    except Exception as e:
        response, status_code = await format_response(
            500, "error", f"An error occurred while retrieving iPhone models: {str(e)}"
        )
        return jsonify(response), status_code


@app.route("/regions", methods=["GET"])
async def get_apple_regions():
    try:
        regions = await api.fetch_and_parse_apple_regions()
        if isinstance(regions, dict) and "error" in regions:
            response, status_code = await format_response(
                400, "error", regions["error"]
            )
        else:
            response, status_code = await format_response(
                200, "success", "Successfully retrieved Apple regions", regions
            )
        return jsonify(response), status_code
    except Exception as e:
        response, status_code = await format_response(
            500, "error", f"An error occurred while retrieving Apple regions: {str(e)}"
        )
        return jsonify(response), status_code


@app.route("/")
async def home():
    return "API is running!"


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8080)
