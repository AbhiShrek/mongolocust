from functools import wraps

from bson.codec_options import TypeRegistry, CodecOptions
from locust import User, between, task

from decimal_codec import DecimalCodec
from settings import DEFAULTS

import time
import pymongo

# singleton Mongo client
CLIENT = pymongo.MongoClient(DEFAULTS['CLUSTER_URL'])


def mongodb_task(weight=1, batch_size=1):
    """
    A decorator to run an operation and time its execution
    :param weight: the task weight
    :param batch_size: how many operations were executed if this was a batch operation
    """
    def middle(func):
        @task(weight=weight)
        def run_mongodb_operation(self):
            start_time = time.time()
            # display the function name as operation name reported in locust stats
            name = func.__name__
            try:
                func(self)
            except Exception as e:
                # output the error for debugging purposes
                print(e)
                total_time = int((time.time() - start_time) * 1000)
                for x in range(batch_size):
                    self.environment.events.request.fire(
                        request_type='mongo', name=name, response_time=total_time, exception=e, response_length=0,
                    )
            else:
                total_time = int((time.time() - start_time) * 1000)
                # ToDo: find a better way of signaling multiple executions to locust and move away from deprecated APIs
                for _ in range(batch_size):
                    self.environment.events.request.fire(
                        request_type='mongodb', name=name, response_time=total_time, response_length=1
                    )

        return run_mongodb_operation

    return middle


class MongoUser(User):
    """
    Base mongodb workload generator
    """
    # this class needs to be marked as abstract, otherwise locust will ry to instantiate it
    abstract = True

    # no think time between calls
    wait_time = between(0.0, 0.0)

    def __init__(self, environment):
        super().__init__(environment)
        self.client = CLIENT
        self.db = CLIENT[DEFAULTS['DB_NAME']]
        # list of collections
        self.collections = [None for collId in range(DEFAULTS['NUM_COLLECTIONS'])]
        # one name cache per collection
        self.cache = [[] for collId in range(DEFAULTS['NUM_COLLECTIONS'])]

    def ensure_collection(self, collId, coll_name, indexes=[]):
        """
        Define the collection and its indexes, return two collections objects:
        one for default read preference, the other with the specified read preference.
        """
        # prepare a codec for decimal values
        decimal_codec = DecimalCodec()
        type_registry = TypeRegistry([decimal_codec])
        codec_options = CodecOptions(type_registry=type_registry)

        # create the collection if not exists
        if coll_name not in self.db.list_collection_names():
            print('[ensure_collection] Created new collection:"',coll_name)
            collection = self.db.create_collection(coll_name, codec_options=codec_options)
            # create the required indexes (only if collection didnt exist)
            if indexes:
                collection.create_indexes(indexes)
        else:
            collection = self.db.get_collection(coll_name, codec_options=codec_options)
            print('[ensure_collection] Found existing collection:"',coll_name)

            if not self.cache[collId]:
                # Retrieve data from the collection and warm up cache
                count = 0;
                for doc in collection.aggregate([ { "$sample": { "size": DEFAULTS['DOCS_TO_CACHE'] } }, { "$project" : { "_id": 1} } ]):
                    self.cache[collId].append(doc['_id'])
                    count += 1
                print("[ensure_collection] Warmed up cache with docs: ", count)

        # also return the second collection with readPreference
        return collection
