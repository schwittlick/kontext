from pymongo import MongoClient


class MongoConnection:
    def __init__(self):
        self.client = MongoClient("mongodb://localhost:27017/")
        self.set_db('kontext')
        self.set_collection('texts2')

    def set_db(self, name):
        self.db = self.client[name]

    def set_collection(self, name):
        self.collection = self.db[name]

    def add_book(self, dict):
        self.collection.insert_one(dict)

    def exists(self, filepath):
        query = {'filename': filepath}
        result = self.collection.find(query)
        if result.count() == 0:
            return False

        return True

    def find(self, query=None):
        return self.collection.find(query)
