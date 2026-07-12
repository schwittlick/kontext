from gensim.models.doc2vec import Doc2Vec, TaggedDocument
from nltk.tokenize import word_tokenize


class DocVec(object):
    def __init__(self):
        self.model = None
        self.modelpath = None
        self.available_models = []
        self.available_models.append("/mnt/drive3/marcel/trained/d2v_size20_epochs2_text2_sandbox_dm0.model")
        self.available_models.append("/mnt/drive3/marcel/trained/d2v_size100_epochs50_ALL_dm0_mincount10.model")
        self.available_models.append("/mnt/drive3/marcel/trained/d2v_fulldocs_size20_epochs1_text2_sandbox_dm0.model")

    def load(self, path):
        print('Loading ' + path)
        self.model = Doc2Vec.load(path)
        self.modelpath = path
        print('Loaded ' + path)

    def ask(self, text, result_amount=100):
        if not self.model:
            return ["Model not loaded"]
        test_data = word_tokenize(text.lower())
        v1 = self.model.infer_vector(test_data)
        similar_doc = self.model.docvecs.most_similar(positive=[v1], topn=result_amount)
        return similar_doc

    def ask_negative(self, text, result_amount=100):
        if not self.model:
            return ["Model not loaded"]
        test_data = word_tokenize(text.lower())
        v1 = self.model.infer_vector(test_data)
        similar_doc = self.model.docvecs.most_similar(negative=[v1], topn=result_amount)
        return similar_doc
