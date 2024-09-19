from quart import Quart, jsonify, request
from quart_cors import cors
import aiohttp
import asyncio
import js2py
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
                print(f"Error occurred while fetching {model}: {str(response)}")
                continue
            if response is None:
                continue
            script_content = response
            start_index = script_content.find("window.PRODUCT_SELECTION_BOOTSTRAP")
            if start_index == -1:
                raise Exception(f"Unable to find product data in {model} page.")

            end_index = script_content.find("</script>", start_index)
            if end_index == -1:
                raise Exception(f"Unable to parse {model} product data.")

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

        print(json.loads(script.string)["props"]["locale"]["allGeoConfigs"])

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


@app.route("/")
async def home():
    return "API is running!"


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8080)
