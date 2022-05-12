import argparse
import copy
import json
import os
import random
import re
import subprocess
from collections import OrderedDict, defaultdict

# Mapping between intents, slots, and relations in English and Chinese
from dialogues.bitod.src.knowledgebase.en_zh_mappings import (
    API_MAP,
    required_slots,
    translation_dict,
    zh2en_CARDINAL_MAP,
    zh_API_MAP,
)
from dialogues.bitod.src.utils import action2span, clean_text, compute_lev_span, knowledge2span, state2span


def translate_slots_to_english(text, do_translate=True):
    if not do_translate:
        return text
    for key, val in translation_dict.items():
        text = text.replace(key, val)
    for key, val in zh_API_MAP.items():
        text = text.replace(key, val)
    for key, val in zh2en_CARDINAL_MAP.items():
        text = text.replace(f'" {key} "', f'" {val} "')
    return text


def get_dials_sequential(args, dials):
    target_lang = args.setting
    all_dial_ids = list(dials.keys())
    if not os.path.exists(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}.json"):
        dial_ids = all_dial_ids[: int(len(all_dial_ids) * args.fewshot_percent // 100)]
        print(f"few shot for {target_lang}, dialogue number: {len(dial_ids)}")
        with open(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}.json", "w") as f:
            json.dump({"fewshot_dials": dial_ids}, f, indent=True)
    else:
        with open(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}.json") as f:
            dial_ids = json.load(f)["fewshot_dials"]
    few_dials = {dial_id: dials[dial_id] for dial_id in dial_ids}
    dials = {dial_id: dials[dial_id] for dial_id in all_dial_ids if dial_id not in dial_ids}

    return dials, few_dials


def get_dials_balanced(args, dials):
    dialogue_dominant_domains = defaultdict(list)
    domain_turn_counts = defaultdict(int)
    for dial_id, dial in dials.items():
        turn_domains = defaultdict(int)
        dialogue_turns = dial["Events"]

        for turn in dialogue_turns:
            if turn["Agent"] == "User":
                active_intent = API_MAP[turn["active_intent"]]

                turn_domains[translate_slots_to_english(active_intent, args.english_slots)] += 1
                domain_turn_counts[translate_slots_to_english(active_intent, args.english_slots)] += 1

        max_domain = max(turn_domains, key=turn_domains.get)
        dialogue_dominant_domains[max_domain].append(dial_id)

    total = sum(list(domain_turn_counts.values()))
    fewshot_dials = []
    all_dial_ids = list(dials.keys())
    for (domain, count) in domain_turn_counts.items():
        num_fewshot = int(len(all_dial_ids) * (count / total) * (args.fewshot_percent / 100))
        fewshot_dials += dialogue_dominant_domains[domain][:num_fewshot]

    target_lang = args.setting
    if not os.path.exists(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}_balanced.json"):
        print(f"balanced few shot for {target_lang}, dialogue number: {len(fewshot_dials)}")
        print("turn statistics:")
        for (domain, count) in domain_turn_counts.items():
            print(domain, "comprises of", count, "or", int(100 * count / total + 0.5), "percent")
        print("few-shot turn statistics:")
        res_turn_counts = defaultdict(int)
        for dial_id in fewshot_dials:
            for turn in dials[dial_id]["Events"]:
                if turn["Agent"] == "User":
                    active_intent = API_MAP[turn["active_intent"]]
                    res_turn_counts[translate_slots_to_english(active_intent, args.english_slots)] += 1
        total = sum(list(res_turn_counts.values()))
        for (domain, count) in res_turn_counts.items():
            print(domain, "comprises of", count, "or", int(100 * count / total + 0.5), "percent")

        with open(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}_balanced.json", "w") as f:
            json.dump({"fewshot_dials": fewshot_dials}, f, indent=True)
            few_dial_ids = fewshot_dials
    else:
        with open(f"data/{target_lang}_fewshot_dials_{args.fewshot_percent}_balanced.json") as f:
            few_dial_ids = json.load(f)["fewshot_dials"]
    few_dials = {dial_id: dials[dial_id] for dial_id in few_dial_ids}
    dials = {dial_id: dials[dial_id] for dial_id in all_dial_ids if dial_id not in few_dial_ids}

    return dials, few_dials


def shorten_path_for_kb(last_dialogue_state, kb_results):
    if 'shortest_path' in kb_results:
        old_shortest_path = kb_results['shortest_path']
        departure, destination = (
            last_dialogue_state['HKMTR en']['departure']['value'][0],
            last_dialogue_state['HKMTR en']['destination']['value'][0],
        )
        new_shortest_path = f"You will depart from {departure} and arrive at {destination}."
        kb_results['shortest_path'] = new_shortest_path
    elif '最短路线' in kb_results:
        old_shortest_path = kb_results['最短路线']
        departure, destination = (
            last_dialogue_state['香港地铁']['出发地']['value'][0],
            last_dialogue_state['香港地铁']['目的地']['value'][0],
        )
        new_shortest_path = f"你将从{departure}出发，抵达{destination}。"
        kb_results['最短路线'] = new_shortest_path

    return old_shortest_path, new_shortest_path


def shorten_path_for_response(target, active_intent, old_shortest_path, new_shortest_path):
    if 'HKMTR en' in active_intent:
        pattern = '[^\.]*?\d+\.\d+[^\.]*\.'
        if old_shortest_path and old_shortest_path in target:
            last_sentence = re.search(pattern, target)
            if last_sentence:
                last_sentence = last_sentence.group().strip('. ')
                target = new_shortest_path + ' ' + last_sentence + '.'
            else:
                target = new_shortest_path
    if '香港地铁' in active_intent:
        pattern = '[^。]*?\d+\.\d+[^。]*。'
        if old_shortest_path and old_shortest_path in target:
            last_sentence = re.search(pattern, target)
            if last_sentence:
                last_sentence = last_sentence.group().strip('。 ')
                target = new_shortest_path + ' ' + last_sentence + '。'
            else:
                target = new_shortest_path

    target = re.sub(' +', ' ', target)
    return target


def shorten_path_for_actions(actions, new_shortest_path):
    for asrv in actions:
        if asrv['slot'] == 'shortest_path':
            asrv['value'] = [new_shortest_path]
        elif asrv['slot'] == '最短路线':
            asrv['value'] = [new_shortest_path]


def read_data(args, path_names, setting, max_history=3):
    print(("Reading all files from {}".format(path_names)))

    # read files
    for path_name in path_names:
        with open(path_name) as file:
            dials = json.load(file)

            new_shortest_path, old_shortest_path = None, None
            data = []
            for dial_id, dial in dials.items():
                dialogue_turns = dial["Events"]

                dialog_history = []
                knowledge = defaultdict(dict)
                last_knowledge_text = "null"
                last_dialogue_state = {}
                count = 1

                intents = []

                turn_id = 0
                while turn_id < len(dialogue_turns):
                    turn = dialogue_turns[turn_id]

                    if turn["Agent"] == "User":
                        if args.gen_full_state:
                            if API_MAP[turn["active_intent"]] not in intents:
                                intents.append(API_MAP[turn["active_intent"]])
                        else:
                            intents = [API_MAP[turn["active_intent"]]]

                        active_intent = intents[-1]

                        # accumulate dialogue utterances
                        if args.use_user_acts:
                            action_text = action2span(turn["Actions"], active_intent, setting)
                            action_text = clean_text(action_text, is_formal=True)
                            action_text = translate_slots_to_english(action_text, args.english_slots)
                            dialog_history.append("USER_ACTS: " + action_text)
                        else:
                            dialog_history.append("USER: " + clean_text(turn["Text"]))

                        if args.last_two_agent_turns and len(dialog_history) >= 4:
                            dial_hist = [dialog_history[-4].replace('AGENT_ACTS:', 'AGENT_ACTS_PREV:')] + dialog_history[-2:]
                        else:
                            dial_hist = dialog_history[-max_history:]

                        dialog_history_text = " ".join(dial_hist)
                        dialog_history_text_for_api_da = dialog_history_text

                        if args.only_user_rg:
                            dialog_history_text_for_rg = dial_hist[-1]
                        else:
                            dialog_history_text_for_rg = dialog_history_text

                        current_state = {API_MAP[k]: v for k, v in turn["state"].items()}
                        current_state = OrderedDict(sorted(current_state.items(), key=lambda s: s[0]))
                        current_state = {
                            k: OrderedDict(sorted(v.items(), key=lambda s: s[0])) for k, v in current_state.items()
                        }

                        # convert dict of slot-values into text
                        state_text = state2span(current_state, required_slots)

                        if args.gen_lev_span:
                            # compute the diff between old state and new state
                            intent = intents[0]
                            target = compute_lev_span(last_dialogue_state, current_state, intent)
                        elif args.gen_full_state:
                            targets = []
                            for intent in intents:
                                targets.append(compute_lev_span({}, current_state, intent))
                            target = ' '.join(targets)
                        else:
                            intent = intents[0]
                            target = compute_lev_span({}, current_state, intent)

                        # update last dialogue state
                        last_dialogue_state = current_state

                        input_text = " ".join(
                            [
                                "DST:",
                                "<state>",
                                translate_slots_to_english(state_text, args.english_slots),
                                "<endofstate>",
                                "<history>",
                                dialog_history_text,
                                "<endofhistory>",
                            ]
                        )

                        dst_data_detail = {
                            "dial_id": dial_id,
                            "task": translate_slots_to_english(active_intent, args.english_slots),
                            "turn_id": count,
                            "input_text": input_text,
                            "output_text": translate_slots_to_english(target, args.english_slots),
                            "train_target": "dst",
                        }

                        data.append(dst_data_detail)

                        turn_id += 1

                    elif turn["Agent"] == "Wizard":

                        # if not args.no_state:
                        #     input_text = " ".join(
                        #         [
                        #             "API:",
                        #             "<knowledge>",
                        #             translate_slots_to_english(last_knowledge_text, args.english_slots),
                        #             "<endofknowledge>",
                        #             "<state>",
                        #             translate_slots_to_english(state_text, args.english_slots),
                        #             "<endofstate>",
                        #             "<history>",
                        #             dialog_history_text_for_api_da,
                        #             "<endofhistory>",
                        #         ]
                        #     )
                        # else:
                        #     input_text = " ".join(
                        #         [
                        #             "API:",
                        #             "<knowledge>",
                        #             translate_slots_to_english(last_knowledge_text, args.english_slots),
                        #             "<endofknowledge>",
                        #             "<history>",
                        #             dialog_history_text_for_api_da,
                        #             "<endofhistory>",
                        #         ]
                        #     )

                        if turn["Actions"] == "query":
                            # do api call
                            # next turn is KnowledgeBase
                            assert dialogue_turns[turn_id + 1]["Agent"] == 'KnowledgeBase'
                            next_turn = dialogue_turns[turn_id + 1]

                            if int(next_turn["TotalItems"]) == 0:
                                last_knowledge_text = f"( {active_intent} ) Message = No item available."
                            else:
                                # they only return 1 item
                                kb_results = next_turn["Item"]
                                if args.shorten_path:
                                    old_shortest_path, new_shortest_path = shorten_path_for_kb(kb_results)

                                knowledge[active_intent].update(kb_results)
                                last_knowledge_text = knowledge2span(knowledge)

                            # api_data_detail = {
                            #     "dial_id": dial_id,
                            #     "task": translate_slots_to_english(active_intent, args.english_slots),
                            #     "turn_id": count,
                            #     "input_text": input_text,
                            #     "output_text": "yes",
                            #     "train_target": "api",
                            # }
                            #
                            # data.append(api_data_detail)

                            # skip knowledge turn since we already processed it
                            turn_id += 2
                            turn = dialogue_turns[turn_id]

                        else:
                            pass

                            # no api call
                            # api_data_detail = {
                            #     "dial_id": dial_id,
                            #     "task": translate_slots_to_english(active_intent, args.english_slots),
                            #     "turn_id": count,
                            #     "input_text": input_text,
                            #     "output_text": "no",
                            #     "train_target": "api",
                            # }
                            #
                            # data.append(api_data_detail)

                        target = clean_text(turn["Text"])
                        actions = turn["Actions"]

                        if args.shorten_path:
                            target = shorten_path_for_response(target, active_intent, old_shortest_path, new_shortest_path)
                            shorten_path_for_actions(actions, new_shortest_path)

                        action_text = action2span(turn["Actions"], active_intent, setting)
                        action_text = clean_text(action_text, is_formal=True)
                        action_text = translate_slots_to_english(action_text, args.english_slots)

                        input_text = " ".join(
                            [
                                "DA:",
                                "<knowledge>",
                                translate_slots_to_english(last_knowledge_text, args.english_slots),
                                "<endofknowledge>",
                                "<actions>",
                                action_text,
                                "<endofactions>",
                                "<history>",
                                dialog_history_text_for_api_da,
                                "<endofhistory>",
                            ]
                        )

                        new_last_dialouge_state = copy.deepcopy(last_dialogue_state)
                        user_re = re.compile('(?:USER|USER_ACTS): (.*?)(?:$| <)')
                        user_text = user_re.search(dialog_history_text_for_api_da).group(1)
                        for intent in last_dialogue_state:
                            for slot in last_dialogue_state[intent]:
                                values = [str(value) for value in last_dialogue_state[intent][slot]["value"]]
                                for val in values:
                                    if val in ["don't care", "不在乎"] and slot in user_text:
                                        continue
                                    elif val == '#unknown':
                                        continue
                                    elif val not in input_text and slot in new_last_dialouge_state[intent]:
                                        new_last_dialouge_state[intent].pop(slot)

                        state_text = state2span(new_last_dialouge_state, required_slots)

                        acts_data_detail = {
                            "dial_id": dial_id,
                            "task": translate_slots_to_english(active_intent, args.english_slots),
                            "turn_id": count,
                            "input_text": input_text,
                            "output_text": translate_slots_to_english(state_text, args.english_slots),
                            "train_target": "da",
                        }
                        data.append(acts_data_detail)

                        input_text = " ".join(
                            [
                                "RG:",
                                "<response>",
                                target,
                                "<endofresponse>",
                                "<history>",
                                dialog_history_text_for_rg,
                                "<endofhistory>",
                            ]
                        )

                        response_data_detail = {
                            "dial_id": dial_id,
                            "task": translate_slots_to_english(active_intent, args.english_slots),
                            "turn_id": count,
                            "input_text": input_text,
                            "output_text": action_text,
                            "train_target": "rg",
                        }
                        data.append(response_data_detail)

                        # update dialogue history
                        if args.use_natural_response:
                            output_text = target
                        else:
                            output_text = action_text

                        dialog_history.append("AGENT_ACTS: " + output_text)

                        turn_id += 1
                        count += 1

    return data


def get_commit():
    directory = os.path.dirname(__file__)
    return (
        subprocess.Popen("cd {} && git log | head -n 1".format(directory), shell=True, stdout=subprocess.PIPE)
        .stdout.read()
        .split()[1]
        .decode()
    )


def prepare_data(args, path_train, path_dev, path_test):
    # "en, zh, en&zh, en2zh, zh2en"
    data_train, data_fewshot, data_dev, data_test = None, None, None, None

    if 'eval' in args.splits:
        data_dev = read_data(args, path_dev, args.setting, args.max_history)
    if 'test' in args.splits:
        data_test = read_data(args, path_test, args.setting, args.max_history)
    if 'train' in args.splits:
        train_data = read_data(args, path_train, args.setting, args.max_history)
        with open(path_train[0]) as file:
            dials = json.load(file)
        if args.sampling == "sequential":
            train_dials, few_dials = get_dials_sequential(args, dials)
        else:
            train_dials, few_dials = get_dials_balanced(args, dials)
        data_train, data_fewshot = [], []
        for data in train_data:
            if data['dial_id'] in train_dials:
                data_train.append(data)
            else:
                data_fewshot.append(data)

    if args.setting == "en_zh":
        if data_train:
            random.shuffle(data_train)
        if data_dev:
            random.shuffle(data_dev)
        if data_test:
            random.shuffle(data_test)

    return data_train, data_fewshot, data_dev, data_test


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, default='dialogues/bitod/', help='data root directory')
    parser.add_argument(
        "--save_dir", type=str, default="data/preprocessed_rev/", help="path to save prerpocessed data for training"
    )
    parser.add_argument("--setting", type=str, default="en", help="en, zh, en_zh")

    parser.add_argument("--max_history", type=int, default=2)
    parser.add_argument("--splits", nargs='+', default=['train', 'eval', 'test'])
    parser.add_argument("--version", type=str, default='11')
    parser.add_argument("--fewshot_percent", type=int, default=0)
    parser.add_argument("--sampling", choices=["sequential", "balanced"], default="sequential")
    parser.add_argument("--use_user_acts", action='store_true')
    parser.add_argument("--gen_lev_span", action='store_true')
    parser.add_argument("--gen_full_state", action='store_true')
    parser.add_argument("--last_two_agent_turns", action='store_true')
    parser.add_argument("--english_slots", action='store_true')
    parser.add_argument("--use_natural_response", action='store_true')
    parser.add_argument("--no_state", action='store_true')
    parser.add_argument("--only_user_rg", action='store_true')

    parser.add_argument("--shorten_path", action='store_true')

    args = parser.parse_args()

    if args.setting in ["en"]:
        path_train = ["data/en_train.json"]
        path_dev = ["data/en_valid.json"]
        path_test = ["data/en_test.json"]
    elif args.setting in ["zh"]:
        path_train = ["data/zh_train.json"]
        path_dev = ["data/zh_valid.json"]
        path_test = ["data/zh_test.json"]
    else:
        path_train = ["data/zh_train.json", "data/en_train.json"]
        path_dev = ["data/zh_valid.json", "data/en_valid.json"]
        path_test = ["data/zh_test.json", "data/en_test.json"]

    path_train = [os.path.join(args.root, p) for p in path_train]
    path_dev = [os.path.join(args.root, p) for p in path_dev]
    path_test = [os.path.join(args.root, p) for p in path_test]

    data_train, data_fewshot, data_dev, data_test = prepare_data(args, path_train, path_dev, path_test)

    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)

    args.commit = get_commit()

    for (set, data) in zip(['train', 'fewshot', 'valid', 'test'], [data_train, data_fewshot, data_dev, data_test]):
        with open(
            os.path.join(
                args.save_dir,
                f"{args.setting}_{set}" + f"_v{args.version}.json",
            ),
            "w",
        ) as f:
            if data:
                json.dump({"args": vars(args), "data": data}, f, indent=True, ensure_ascii=False)
                print(set, len(data))

    # with open(os.path.join(f"./data_samples/v{args.version}.json"), "w") as f:
    #     json.dump({"data": data_test[:30]}, f, indent=True, ensure_ascii=False)


if __name__ == "__main__":
    main()