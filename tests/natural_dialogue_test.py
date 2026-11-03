"""Both prompt variants retain natural-chat rules and role boundaries."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.prompting import build_system_prompt,build_compact_system_prompt,natural_dialogue_rule
from app.defaults import ARONA_PERSONA,PLANA_PERSONA


def main():
    for persona in (ARONA_PERSONA,PLANA_PERSONA,{'name':'阿米娅','system_prompt':'称呼对方为博士。'}):
        for builder in (build_system_prompt,build_compact_system_prompt):
            prompt=builder(persona,[],recipient_name='测试对象',max_chars=25,max_sentence_chars=25)
            assert natural_dialogue_rule(persona) in prompt
            assert all(x in prompt for x in ['25','不把一个群友的经历','对方说不想提就收住','需要核实的事实照常','不机械加结尾提问'])
            assert persona['name'] in prompt
        assert '不要为了显得像真人，编造现实经历' in natural_dialogue_rule(persona)
    print('NATURAL DIALOGUE TESTS PASSED')


if __name__=='__main__':main()
