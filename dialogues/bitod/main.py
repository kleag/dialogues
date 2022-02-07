import logging
import os.path
import re
from collections import defaultdict

from ..main import Dataset
from .BiToD.knowledgebase import api
from .BiToD.knowledgebase.en_zh_mappings import r_en_API_MAP, api_names, required_slots
from .BiToD.preprocess import prepare_data
from .BiToD.evaluate import eval_file
from .BiToD.utils import state2constraints, state2span, action2span, knowledge2span, span2state, span2action, span2knowledge


logger = logging.getLogger(__name__)

class Bitod(Dataset):
	def __init__(self, name='bitod'):
		super().__init__(name)

		self.state_re = re.compile('<state> (.*?)(?:$|<)')
		self.knowledge_re = re.compile('<knowledge> (.*?)(?:$|<)')
		self.actions_re = re.compile('<actions> (.*?)(?:$|<)')
		self.api_names = api_names
		self.required_slots = required_slots
	
	def domain2api_name(self, domain):
		return r_en_API_MAP.get(domain, domain)
	
	def state2span(self, dialogue_state):
		return state2span(dialogue_state, self.required_slots)
	
	def span2state(self, lev):
		return span2state(lev, self.api_names)
	
	def process_data(self, args, root):
		if args.setting in ["en", "zh2en"]:
			path_train = ["data/en_train.json"]
			path_dev = ["data/en_valid.json"]
			path_test = ["data/en_test.json"]
		elif args.setting in ["zh", "en2zh"]:
			path_train = ["data/zh_train.json"]
			path_dev = ["data/zh_valid.json"]
			path_test = ["data/zh_test.json"]
		else:
			path_train = ["data/zh_train.json", "data/en_train.json"]
			path_dev = ["data/zh_valid.json", "data/en_valid.json"]
			path_test = ["data/zh_test.json", "data/en_test.json"]
			
		path_train = [os.path.join(root, p) for p in path_train]
		path_dev = [os.path.join(root, p) for p in path_dev]
		path_test = [os.path.join(root, p) for p in path_test]
		
		train, dev, test = prepare_data(args, path_train, path_dev, path_test)
		return train, dev, test
	
	def make_api_call(self, dialogue_state, api_name, src_lang='en', dial_id=None, turn_id=None):
		knowledge = defaultdict(dict)
		result = [0, 0, 0]
		
		constraints = state2constraints(dialogue_state[api_name])
		
		try:
			result = api.call_api(r_en_API_MAP.get(api_name, api_name), constraints=[constraints], lang=src_lang)
		except Exception as e:
			logger.error(f'Error: {e}')
			logger.error(
				f'Failed API call with api_name: {api_name}, constraints: {constraints}, processed_query: {result[2]}, for turn: {dial_id}/{turn_id}'
			)
		
		if int(result[1]) <= 0:
			logger.warning(
				f'Message = No item available for api_name: {api_name}, constraints: {constraints},'
				f' processed_query: {result[2]}, for turn: {dial_id}/{turn_id}'
			)
			new_knowledge_text = f'( {api_name} ) Message = No item available.'
		else:
			# always choose the highest ranking results (so we have deterministic api results)
			knowledge[api_name].update(result[0])
			new_knowledge_text = knowledge2span(knowledge)
		
		return constraints, new_knowledge_text
	
	def compute_metrics(self, args, prediction_path, reference_path):
		results = eval_file(args, prediction_path, reference_path)
		return results
	
	def postprocess_prediction(self, prediction, knowledge, lang):
		
		if (
			re.search(rf'\( HKMTR {lang} \)', prediction)
			and 'offer shortest_path equal_to' in prediction
		):
			action_dict = span2action(prediction, self.api_names)
			domain = f'HKMTR {lang}'
			metro_slots = set(item['slot'] for item in action_dict[domain])
			for slot in ['estimated_time', 'price']:
				if knowledge and slot in knowledge[domain] and slot not in metro_slots:
					action_dict[domain].append(
						{'act': 'offer', 'slot': slot, 'relation': 'equal_to', 'value': [knowledge[domain][slot]]}
					)
			
			prediction = action2span(action_dict[domain], domain, lang)
		
		if (
			re.search(r'\( weathers search \)', prediction)
			and 'offer weather equal_to' in prediction
		):
			action_dict = span2action(prediction, self.api_names)
			domain = 'weathers search'
			weather_slots = set(item['slot'] for item in action_dict[domain])
			for slot in ['max_temp', 'min_temp']:
				if knowledge and slot in knowledge[domain] and slot not in weather_slots:
					action_dict[domain].append(
						{'act': 'offer', 'slot': slot, 'relation': 'equal_to', 'value': [knowledge[domain][slot]]}
					)
			
			prediction = action2span(action_dict[domain], domain, lang)
			
		return prediction

