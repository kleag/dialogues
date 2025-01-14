import argparse
from collections import defaultdict

import dictdiffer

from dialogues import Risawoz

parser = argparse.ArgumentParser()
parser.add_argument("--setting", type=str, default="en", help="en, zh, en_zh, en2zh, zh2en")
args = parser.parse_args()


def api_result_diff(knowledge, gold_knowledge):
    knowledge = dataset.span2knowledge(knowledge)
    processed_knowledge = defaultdict(dict)
    for d in gold_knowledge.keys():
        gold_knowledge[d] = {s: str(v) for s, v in gold_knowledge[d].items()}
    for d in knowledge.keys():
        for sv in knowledge[d]:
            processed_knowledge[d][sv['slot']] = str(sv['value'][0])
    return list(dictdiffer.diff(dict(processed_knowledge), gold_knowledge))


api_list = ["hotel", "attraction"]

if args.setting == 'zh':
    dialogue_state = {
        "hotel": {'pricerange': {'relation': 'equal_to', 'value': ['偏贵']}, 'area': {'relation': 'equal_to', 'value': ['吴江']}},
        "attraction": {"name": {'relation': 'equal_to', 'value': ['金鸡湖景区']}},
    }
    gold_knowledge = {
        'hotel': {
            "name": "苏州黎里水岸寒舍精品酒店",
            "area": "吴江",
            "star": "5",
            "pricerange": "偏贵",
            "hotel_type": "商务出行",
            "room_type": "大床房",
            "parking": "免费",
            "room_charge": "629 元",
            "address": "苏州吴江区黎里镇南新街 5-9 号",
            "phone_number": "180-5181-5602",
            "score": 4.6,
            "available_options": 4,
        },
        'attraction': {
            "name": "金鸡湖景区",
            "area": "工业园区",
            "type": "山水景区",
            "the_most_suitable_people": "情侣约会",
            "consumption": "偏贵",
            "metro_station": "是",
            "ticket_price": "免费",
            "phone_number": "400-7558558",
            "address": "苏州市工业园区星港街 158 号",
            "score": 4.5,
            "opening_hours": "全天",
            "features": "看东方之门等高楼，坐摩天轮，乘船夜游，感受苏州现代化的一面。",
            "available_options": 1,
        },
    }
elif args.setting == 'en':
    dialogue_state = {
        "hotel": {
            'pricerange': {'relation': 'equal_to', 'value': ['slightly expensive']},
            'area': {'relation': 'equal_to', 'value': ['Wujiang District']},
        },
        "attraction": {"name": {'relation': 'equal_to', 'value': ['Jinji Lake Scenic Area']}},
    }
    gold_knowledge = {
        'hotel': {
            "name": "Suzhou Shui'an Hanshe Boutique Hotel",
            "area": "Wujiang District",
            "star": "5",
            "pricerange": "slightly expensive",
            "hotel_type": "business",
            "room_type": "king-size room",
            "parking": "free",
            "room_charge": "629 yuan",
            "address": "No. 5-9, Nanxin Street, Lili Town, Wujiang District, Suzhou",
            "phone_number": "180-5181-5602",
            "score": "4.6",
            "available_options": 4,
        },
        'attraction': {
            "name": "Jinji Lake Scenic Area",
            "area": "Suzhou Industrial Park",
            "type": "landscape scenic spot",
            "the_most_suitable_people": "dating",
            "consumption": "slightly expensive",
            "metro_station": "true",
            "ticket_price": "free",
            "phone_number": "400-7558558",
            "address": "No.158, Xinggang Street, Suzhou Industrial Park, Suzhou City",
            "score": "4.5",
            "opening_hours": "all day",
            "features": "get a good view of tall buildings like the Gate of the Orient, ride the Ferris wheel, take a night cruise, and feel the modern side of Suzhou.",
            "available_options": 1,
        },
    }

knowledge = defaultdict(dict)

dataset = Risawoz()
new_knowledge_text, constraints = dataset.make_api_call(dialogue_state, knowledge, api_list, src_lang=args.setting)


api_diff = api_result_diff(new_knowledge_text, gold_knowledge)
print('diff:', api_diff)
assert len(api_diff) == 0
