import copy
import unittest
from scripts.persona_blind_review import summarize


class BlindReviewTests(unittest.TestCase):
    def fixture(self):
        cases=[];reviews=[];mapping={}
        for number in range(32):
            ident='H'+str(number+1).zfill(2)
            cases.append(dict(id=ident,A=['first answer','second answer'],B=['another answer','last answer']))
            def grade(text):
                return dict(natural=True,evidence=[dict(turn=1,quote=text,reason='Specific review evidence')],clear_errors=[])
            reviews.append(dict(id=ident,A=grade('first answer'),B=grade('another answer'),winner='A' if number<20 else 'tie',preference_reason='Specific comparative reason'))
            mapping[ident]='A'
        return {'cases':cases},{'cases':reviews},mapping

    def test_ties_remain_in_denominator(self):
        pack,review,mapping=self.fixture()
        result=summarize(pack,review,mapping)
        self.assertEqual(result['totals']['candidate']['win_fraction'],20/32)
        self.assertTrue(result['holdout_quality_gate_passed'])
        review['cases'][19]['winner']='tie'
        self.assertFalse(summarize(pack,review,mapping)['holdout_quality_gate_passed'])

    def test_single_clear_error_fails_gate(self):
        pack,review,mapping=self.fixture()
        review['cases'][0]['A']['natural']=False
        review['cases'][0]['A']['clear_errors']=[dict(turn=1,quote='first answer',reason='Explicit capability error')]
        self.assertFalse(summarize(pack,review,mapping)['holdout_quality_gate_passed'])

    def test_missing_case_cannot_improve_score(self):
        pack,review,mapping=self.fixture()
        review['cases'].pop()
        with self.assertRaises(ValueError):summarize(pack,review,mapping)

    def test_fabricated_quote_and_false_natural_grade_rejected(self):
        pack,review,mapping=self.fixture()
        changed=copy.deepcopy(review)
        changed['cases'][0]['A']['evidence'][0]['quote']='invented reply'
        with self.assertRaises(ValueError):summarize(pack,changed,mapping)
        changed=copy.deepcopy(review)
        changed['cases'][0]['A']['clear_errors']=[dict(turn=1,quote='first answer',reason='Clear error')]
        with self.assertRaises(ValueError):summarize(pack,changed,mapping)


if __name__=='__main__':unittest.main()
