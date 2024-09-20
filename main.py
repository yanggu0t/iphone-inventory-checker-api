from quart import Quart, jsonify, request
from quart_cors import cors
import aiohttp
import asyncio
import execjs
import re
import json
from bs4 import BeautifulSoup

app = Quart(__name__)
app = cors(app)


class iPhoneModelsAPI:
    def __init__(self):
        self.base_url = "https://www.apple.com"
        self.iphone_url = self.base_url + "/{lang}/shop/buy-iphone/{model}"
        self.locales_url = self.base_url + "/retail/storelist"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        self.models = ["iphone-16", "iphone-16-pro"]
        self.color_info = {}
        self.image_base_url = "https://store.storeimages.cdn-apple.com/4982/as-images.apple.com/is/"

    def get_language(self, accept_language):
        primary_lang = accept_language.split(",")[0].strip()
        return primary_lang.lower()

    async def fetch_model(self, session, lang, model):
        url = self.iphone_url.format(lang=lang, model=model)
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                raise Exception(
                    f"Unable to fetch {model} model data. Please check your network connection."
                )
            return await response.text()

    async def fetch_models(self, lang):
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_model(session, lang, model) for model in self.models]
            responses = await asyncio.gather(*tasks)

        all_models = {}
        for model, response in zip(self.models, responses):
            if isinstance(response, Exception):
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

            # 創建一個模擬的 window 對象
            js_code = "var window = {};\n" + js_code

            ctx = execjs.compile(js_code)
            product_data = ctx.eval("window.PRODUCT_SELECTION_BOOTSTRAP")

            all_models[model] = product_data["productSelectionData"]["products"]
            self.color_info[model] = product_data["productSelectionData"]["displayValues"]["dimensionColor"]

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
                image_url = self.image_base_url+product['imageKey']

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
                    "image_url":image_url
                }
                if part_info not in model_info[model_name]["part_numbers"]:
                    model_info[model_name]["part_numbers"].append(part_info)

            # Convert to list and sort
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

        # Sort by model name
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
        return model_id  # If unable to parse, return original ID

    async def get_models(self, lang):
        data = await self.fetch_models(lang)
        return self.parse_models(data)

    async def fetch_and_parse_apple_regions(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.locales_url, headers=self.headers) as response:
                if response.status != 200:
                    return {
                        "error": f"Unable to fetch webpage. Status code: {response.status}"
                    }
                content = await response.text()

        soup = BeautifulSoup(content, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")

        if not script:
            return {"error": "Unable to find script tag containing region information"}

        try:
            data = json.loads(script.string)
            all_geo_configs = data["props"]["locale"]["allGeoConfigs"]
        except (json.JSONDecodeError, KeyError):
            return {"error": "Unable to parse JSON data or find required information"}

        # Define list of IDs to exclude
        excluded_ids = ["en_HK", "en_MO", "zh_MO", "fr_CA", "nl_BE", "fr_CH"]

        result = []
        for geo_id, geo_config in all_geo_configs.items():
            # Check if geo_id is in the excluded list
            if geo_id not in excluded_ids:
                # Special handling for zh_CN
                lang_tag = (
                    "cn"
                    if geo_id == "zh_CN"
                    else geo_config.get("storeRootPath", "").strip("/")
                )

                region = {
                    "id": geo_id,
                    "country": geo_config.get("territory", ""),
                    "lang_tag": lang_tag,
                }
                result.append(region)

        # Sort results by country name
        result.sort(key=lambda x: x["country"])

        return result

    async def fetch_config(self, lang):
        url = self.base_url + f"/{lang}/shop/buy-iphone/iphone-16-pro/"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception("無法獲取配置數據。請檢查您的網絡連接。")
                script_content = await response.text()
    
        print(f"獲取的網頁內容長度: {len(script_content)}")
    
        # 使用正則表達式查找並提取 JavaScript 對象
        match = re.search(r'window\.fulfillmentBootstrap\s*=\s*({.*?});', script_content, re.DOTALL)
        if not match:
            raise Exception("無法找到產品數據。")
    
        js_object = match.group(1)
    
        try:
            # 使用 PyExecJS 解析 JavaScript 對象
            ctx = execjs.compile(f"var data = {js_object}")
            content_data = ctx.eval("data")
            
            # 提取我們需要的特定數據
            search_data = {
                "pickupURL": content_data.get("pickupURL", ""),
                "modelMessage": content_data.get("modelMessage", ""),
                "validation": content_data.get("validation", {}),
                "zipMessage"   : content_data.get("searchPlaceholder", ""),
                "searchButton"   : content_data.get("searchButton", ""),
                "suggestionsURL"   : content_data.get("suggestionsURL", ""),
            }
                        
            # return config_data
            return {"search":search_data}
        except Exception as e:
            print(f"處理數據時發生錯誤: {str(e)}")
            raise

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


@app.route("/locales", methods=["GET"])
async def get_apple_regions():
    try:
        regions = await api.fetch_and_parse_apple_regions()
        if isinstance(regions, dict) and "error" in regions:
            response, status_code = await format_response(
                400, "error", regions["error"]
            )
        else:
            response, status_code = await format_response(
                200,
                "success",
                "Successfully retrieved Apple region information",
                regions,
            )
        return jsonify(response), status_code
    except Exception as e:
        response, status_code = await format_response(
            500,
            "error",
            f"An error occurred while retrieving Apple region information: {str(e)}",
        )
        return jsonify(response), status_code
    

@app.route("/config", methods=["GET"])
async def get_config():
    try:
        accept_language = request.headers.get("Accept-Language", "")
        lang = api.get_language(accept_language)
        config = await api.fetch_config(lang)
        response, status_code = await format_response(
            200, "success", "Successfully retrieved configuration information", config
        )
        return jsonify(response), status_code
    except Exception as e:
        response, status_code = await format_response(
            500, "error", f"An error occurred while retrieving configuration information: {str(e)}"
        )
        return jsonify(response), status_code


@app.route("/")
async def home():
    return "API is running!"


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8080)
